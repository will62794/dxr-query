#!/usr/bin/python
import pprint
from graphviz import Digraph
from elasticsearch import Elasticsearch
import argparse
import hashlib

#
# Query an Elasticsearch database instance that houses the clang static analysis info
# used by the Mozilla DXR tool.
#


"""
Building a callgraph:

The current approach for constructing a call graph is as follows.

1. Pick a desired function F to start from e.g. 'mongo::repl::logOp'.
2. Find all references to F that call the function.
3. For each reference found, determine the enclosing function for that reference i.e. what
function does the reference live inside of.
4. For each enclosing function, go back to step 2 and repeat until no more refs are found.

"""

TRACE = False

es = Elasticsearch(["10.1.2.40:9200"])
indices = [x for x in es.indices.get('*') if "mongodb_v" not in x and "mongodb_" in x]
# this changes every day i think so we should probably dynamically retrieve it.
# mongo_master_index = 'dxr_19_mongodb_66eff114-bda0-11e9-94a3-0cc47ad955d7'
mongo_master_index = indices[0]

def shortest_str(lst):
	""" Return the shortest string in a given list. """
	return min(lst, key=lambda s : len(s))

def trace(msg):
	if TRACE:
		print msg

def multi_match_query(conditions):
	""" Build a query that matches all given conditions."""
	must = [{"match": cond} for cond in conditions]
	return {
		"size": 1000,
		"query": {
			"bool": {"must": must}
		}
	}

def find_line_by_qualname(qualname):
	doc = {
		"size": 1000,
		"query": {"match":{"c_function.qualname": qualname}}
	}
	res = es.search(index=mongo_master_index, body=doc,scroll='1m')
	trace("'find_line_by_qualname' query took: %dms" % res["took"])
	hits = res["hits"]["hits"]
	return hits[0]	

def find_refs(qualname):
	""" Look up all references to a given function, given its qualified name. """
	doc = {
		"size": 1000,
		"query": {"match":{"c_function_ref.qualname": qualname}}
	}
	res = es.search(index=mongo_master_index, body=doc,scroll='1m')
	trace("'find_refs' query took: %dms" % res["took"])
	hits = res["hits"]["hits"]
	return hits

def find_file(path):
	""" Find a file document by path. """
	doc = multi_match_query([{"path": path}, {"_type": "file"}])
	res = es.search(index=mongo_master_index, body=doc,scroll='1m')
	trace("'find_file' query took: %dms" % res["took"])
	hits = res["hits"]["hits"]
	return hits[0]

def find_enclosing_function(line):
	""" Try to find the enclosing function definition for a given line. """
	filepath = line["_source"]["path"][0]
	doc = {
		"size": 1000,
		"query": {
			"bool": {
			  "must": [
				{"exists": { "field": "c_function" }},
				{"match" : {"path": filepath}}
			  ]
			}
		}
	}
	res = es.search(index=mongo_master_index, body=doc,scroll='1m')
	trace("'find_enclosing_function' query took: %dms" % res["took"])
	hits = res["hits"]["hits"]
	# Go through all function references in the file.
	line_nums = []
	for h in hits:
		line_nums.append(h["_source"]["number"][0])

	# Find the greatest line number less than the base_line i.e. the first function definition
	# above the target line.
	base_line = line["_source"]["number"][0]
	fn_line = max(filter(lambda ln : ln < base_line, line_nums))

	# Query again for the function def line.
	doc = multi_match_query([{"number": fn_line}, {"path": filepath}])
	res = es.search(index=mongo_master_index, body=doc,scroll='1m')
	trace("'find fn_line' query took: %dms" % res["took"])
	hits = res["hits"]["hits"]
	return hits[0]

def find_callers(line):
	""" Starting from a function definition line, find all callers i.e. the enclosing functions of all call sites. """
	qualname = line["_source"]["c_function"][0]["qualname"]
	if isinstance(qualname, list):
		qualname = qualname[0]

	# Find all references to this function.
	refs = find_refs(qualname)
	callers = []
	for ref in refs:
		# print "---", h['_source']['path'], h['_source']['number'], "---"
		# Skip unit test files for now.
		if "_test" in ref["_source"]['path'][0] or "tests" in ref["_source"]['path'][0]:
			continue

		# Look for all lines in this file that are function definitions.
		enclosing_line = ref
		caller_fn = find_enclosing_function(enclosing_line)
		# Include both the calling function and the callsite itself.
		callers.append({"caller": caller_fn, "call": ref})
		qualname = caller_fn["_source"]["c_function"][0]['qualname'][0]
		c_function = caller_fn["_source"]["c_function"][0]
	return callers

def make_call_dot_graph(edges):
	dot = Digraph(comment='Call Graph', strict=True) # de-duplicate edges with 'strict'
	dot.graph_attr['rankdir'] = 'LR'
	nodes = {}
	# caller, callee
	for (i, j) in edges:
		if i["call"] is not None:
			f = find_file(i["call"]["_source"]["path"][0])
			i_link = get_file_link(f, i["call"]["_source"]["number"][0])
		else:
			f = find_file(i["caller"]["_source"]["path"][0])
			i_link = get_file_link(f, i["caller"]["_source"]["number"][0])
		
		if j["call"] is not None:
			f = find_file(j["call"]["_source"]["path"][0])
			j_link = get_file_link(f, j["call"]["_source"]["number"][0])
		else:
			f = find_file(j["caller"]["_source"]["path"][0])
			j_link = get_file_link(f, j["caller"]["_source"]["number"][0])
		# print i["_id"]
		ifn, jfn = (i["caller"]["_source"]["c_function"][0], j["caller"]["_source"]["c_function"][0])
		# Pick the shortest qualified name.
		# i_label_name = shortest_str([s for s in ifn['qualname'] if len(s)])
		# j_label_name = shortest_str([s for s in jfn['qualname'] if len(s)])

		# Maybe use qualified names? Maybe short names are good enough.
		i_label_name = ifn['name']
		j_label_name = jfn['name']

		# If the qualified name is in an anonymous namespace, just use the short name, since the
		# static function version is way too verbose.
		if "anonymous" in i_label_name:
			i_label_name = ifn["name"]
		if "anonymous" in j_label_name:
			j_label_name = jfn["name"]

		dot.edge(i["caller"]["_id"], j["caller"]["_id"])
		# No need to add nodes twice.
		if i["caller"]["_id"] not in nodes:
			dot.node(i["caller"]["_id"], URL=i_link, label=i_label_name)
			nodes[i["caller"]["_id"]]=True
		if j["caller"]["_id"] not in nodes:
			dot.node(j["caller"]["_id"], URL=j_link, label=j_label_name)
			nodes[j["caller"]["_id"]]=True
	return dot.source

def get_file_link(f, line_num):
	""" Get versioned Github link for file. """
	links = f["_source"]["links"]
	vcs_links = filter(lambda l : l["heading"]=="VCS Links", links)[0]["items"]
	revlink = filter(lambda l : l["title"]=="Normal", vcs_links)[0]
	return revlink["href"].replace("{{line}}", str(line_num))

def build_call_graph(qualname):
	# 1. Start with a target function.
	line = find_line_by_qualname(qualname)
	root = {"caller": line, "call": None} #  the root doesn't have a call site.
	fn_queue = [root]
	edges = []
	# 2. Keep processing nodes while we have more functions to explore in the tree. This is
	# basically a breadth first traversal of the tree.
	while len(fn_queue)>0:
		curr_fn_line = fn_queue.pop(0)
		caller_fns = find_callers(curr_fn_line["caller"])
		# Push each calling function onto the back of the queue and save the edge.
		for c in caller_fns:
			# edge: (caller, callee)
			edge = (c, curr_fn_line)
			edges.append(edge)
			fn_queue.append(c)
	return edges, root

########################################
#
# COMMAND LINE INTERFACE.
#
########################################
	
def line_no(line):
	""" Return prettified line number from line object. """
	source = line["_source"]
	return "%s:%s" % (source["path"][0], source["number"][0])

def callers(qualname):
	""" Find all callers by qualified name and print in a readable format."""
	line = find_line_by_qualname(qualname)
	callers = find_callers(line)
	print "Found %d callers of '%s':" % (len(callers), qualname)
	for c in callers:
		print c["caller"]["_source"]['c_function'][0]['name'],",",line_no(c["caller"])

def calls(qualname, github_links=False):
	""" Find all calls of a given function."""
	refs = find_refs(args["calls"])
	print "Found %d calls to '%s':" % (len(refs), qualname)
	for ref in refs:
		source = ref["_source"]
		path, line_num = (source["path"][0], source["number"][0])
		print "%s:%s" % (path, line_num)
		if github_links:
			file = find_file(path)
			link = get_file_link(file, line_num)
			print link

def print_tree(edges, node, depth=0, max_depth=10):
	""" Print a call tree rooted at 'node'."""
	if depth >= max_depth:
		return
	# Print the current node.
	non_empty_qualnames = [s for s in node["caller"]["_source"]["c_function"][0]["qualname"] if len(s)>0]
	root_qualname = shortest_str(non_empty_qualnames)
	if root_qualname=="":
		print node["caller"]["_source"]["c_function"][0]["qualname"]
	assert len(root_qualname)>0
	print ("||" * (depth) + "> ") + root_qualname
	caller_edges = filter(lambda (i,j) : root_qualname in j["caller"]["_source"]["c_function"][0]["qualname"], edges)
	for (caller, callee) in caller_edges:
		print_tree(edges, caller, depth=(depth+1))
		# print shortest_str(caller["_source"]["c_function"][0]["qualname"])

def calltree(qualname, depth):
	edges, root = build_call_graph(qualname)
	print_tree(edges, root)

def dot_calltree(qualname, depth):
	edges, root = build_call_graph(qualname)
	print make_call_dot_graph(edges)

def cmdline_args():
	# Make parser object
	p = argparse.ArgumentParser(description="Query DXR database.",
								formatter_class=argparse.ArgumentDefaultsHelpFormatter)

	p.add_argument("--callers", type=str, help="find all callers of function")
	p.add_argument("--calls", type=str, help="find all calls to a function")
	p.add_argument("--calltree", type=str, help="show calltree starting from a function")
	p.add_argument("--dotcalltree", type=str, help="show DOT call graph starting from a function")

	return(p.parse_args())

if __name__ == '__main__':
	TRACE = False

	args = vars(cmdline_args())

	# Dispatch to different commands.
	if args["callers"]:
		callers(args["callers"])

	if args["calls"]:
		calls(args["calls"], github_links=True)

	if args["calltree"]:
		calltree(args["calltree"], 4)

	if args["dotcalltree"]:
		dot_calltree(args["dotcalltree"], 4)

