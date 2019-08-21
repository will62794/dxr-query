#!/usr/bin/python
import pprint
from graphviz import Digraph
from elasticsearch import Elasticsearch    
import argparse

#
# Query the Elasticsearch database instance that houses the clang static analysis info
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


def trace(msg):
	if TRACE:
		print msg

def find_line_by_qualname(qualname):
	doc = {
		"size": 10,
		"query": {"match":{"c_function.qualname": qualname}}
	}
	res = es.search(index=mongo_master_index, body=doc,scroll='1m')
	trace("'find_line_by_qualname' query took: %dms" % res["took"])
	hits = res["hits"]["hits"]
	return hits[0]	

def find_refs(qualname):
	""" Look up all references to a given function, given its qualified name. """
	doc = {
		"size": 10,
		"query": {"match":{"c_function_ref.qualname": qualname}}
	}
	res = es.search(index=mongo_master_index, body=doc,scroll='1m')
	trace("'find_refs' query took: %dms" % res["took"])
	hits = res["hits"]["hits"]
	return hits

def find_file(path):
	""" Find a file document by path. """
	doc = {
	"size": 100,
	"query": {
	    "bool": {
	      "must": [
	        {
	          "match": {
	            "path": path
	          }
	        },
	        {
	          "match": {
	            "_type": "file"
	          }
	        }
	      ]
	    }
		}
	}
	res = es.search(index=mongo_master_index, body=doc,scroll='1m')
	# print "Query took: %dms" % res["took"]
	hits = res["hits"]["hits"]
	return hits[0]

def find_enclosing_function(line):
	""" Try to find the enclosing function definition for a given line. """
	filepath = line["_source"]["path"][0]
	doc = {
	"size": 100,
	"query": {
	    "bool": {
	      "must": [
	        {
	          "exists": {
	            "field": "c_function"
	          }
	        },
	        {
	          "match": {
	            "path": filepath
	          }
	        }
	      ]
	    }
		}
	}
	# print doc
	res = es.search(index=mongo_master_index, body=doc,scroll='1m')
	trace("'find_enclosing_function' query took: %dms" % res["took"])
	hits = res["hits"]["hits"]
	# Go through all function references in the file.
	line_nums = []
	for h in hits:
		# pprint.pprint(h["_source"]["c_function"])
		# pprint.pprint(h["_source"]["number"])
		# print h["_source"]["number"][0]
		line_nums.append(h["_source"]["number"][0])

	# Find the greatest line number less than the base_line i.e. the first function definition
	# above the target line.
	base_line = line["_source"]["number"][0]
	fn_line = max(filter(lambda ln : ln < base_line, line_nums))
	# print "Function line: ", fn_line

	# Query again for the function def line.
	doc = {
		"size": 1000,
		"query": {
		    "bool": {
		      "must": [
		        {
		          "match": {
		            "number": fn_line
		          }
		        },
		        {
		          "match": {
		            "path": filepath
		          }
		        }
		      ]
		    }
		}
	}

	# print fn_line
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
		callers.append(caller_fn)
		qualname = caller_fn["_source"]["c_function"][0]['qualname'][0]
		c_function = caller_fn["_source"]["c_function"][0]
		# pprint.pprint(fn["_source"])
		# pprint.pprint(caller_fn["_source"]["c_function"][0]['name'])
		# pprint.pprint(caller_fn["_source"]["c_function"][0]['qualname'])

		# print c_function["name"]
		# print len(caller_fn["_source"]["c_function"])
		# hits = find_refs(qualname)
		# call_graph_edges[c_function["name"]] = line["_source"]["c_function"][0]["name"]
		# find_callers(caller_fn)
		# print "Refs: %d" % len(hits)
		# print "Enclosing function line:", caller_fn["_source"]["number"]
	return callers

def add_callers(lines, call_graph, depth):
	""" Starting from a given list of lines which are function definitions, and add all its callers to the call graph. """ 
	# Start by having some arbitrary depth limit on recursion.
	if depth >= 8:
		return
	for line in lines:
		callers = find_callers(line)
		for c in callers:
			# call_graph[c["_source"]["c_function"][0]['name']] = line["_source"]["c_function"][0]['name']
			call_graph.append((c, line))
		# Recursively add callers for all the callers.
		add_callers(callers, call_graph, depth + 1)

def make_call_dot_graph(edges):
	dot = Digraph(comment='Call Graph', strict=True) # de-duplicate edges with 'strict'
	dot.graph_attr['rankdir'] = 'LR'
	for (i, j) in edges:
		f = find_file(i["_source"]["path"][0])[0]
		link = get_file_link(f, i["_source"]["number"][0])

		# dot.node(edge)
		dot.edge(i["_source"]["c_function"][0]['name'], j["_source"]["c_function"][0]['name'])
		dot.node(i["_source"]["c_function"][0]['name'], URL=link)
	return dot.source

def get_file_link(f, line_num):
	""" Get versioned Github link for file. """
	links = f["_source"]["links"]
	vcs_links = filter(lambda l : l["heading"]=="VCS Links", links)[0]["items"]
	revlink = filter(lambda l : l["title"]=="Normal", vcs_links)[0]
	return revlink["href"].replace("{{line}}", str(line_num))

def build_call_graph(target_qualname):
	line = find_line_by_qualname(target_qualname)
	print "* Finding callers for qualified name '%s'" % target_qualname
	callers = find_callers(line)
	print "Found %d callers." % len(callers)
	for c in callers:
		print c["_source"]["c_function"][0]["name"], ",", c["_source"]["path"][0]

	# Store graph as a list of edges i.e. tuples.
	call_graph_edges = []
	depth = 0
	add_callers([line], call_graph_edges, depth)
	return call_graph_edges

def build_call_tree(qualname):
	# 1. Start with a target function.
	line = find_line_by_qualname(qualname)
	fn_queue = [line]
	edges = []
	# 2. Keep processing nodes while we have more functions to explore in the tree.
	while len(fn_queue)>0:
		curr_fn_line = fn_queue.pop(0)
		print curr_fn_line["_source"]["path"]
		caller_fns = find_callers(curr_fn_line)
		# Push each calling function onto the back of the queue.
		for c in caller_fns:
			edge = (curr_fn_line, c)
			edges.append(edge)
			fn_queue.append(c)
	return edges

def cg_test():
	# Do a query.
	doc = {
		"size": 10,
		"query": {"match":{
					"file_name": "session_catalog_migration_destination.cpp"}}
	}
	res = es.search(index=mongo_master_index, body=doc,scroll='1m')
	hits = res["hits"]["hits"]
	print "Found %d hits for query %s" % (len(hits), str(doc))
	C = []
	c1 = [] # build_call_graph("mongo::OperationContext::setInMultiDocumentTransaction")
	c2 = build_call_graph("mongo::RecordStore::insertRecords")
	# c2 = build_call_graph("mongo::OperationContext::getDeadline")
	# C.append(build_call_graph("mongo::OperationContext::getElapsedTime"))
	print make_call_dot_graph(c1+c2)

########################################
#
# COMMAND LINE INTERFACE.
#
########################################

def cmdline_args():
    # Make parser object
    p = argparse.ArgumentParser(description=
        """
        Query DXR database.
        """,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    
    p.add_argument("--callers", type=str, help="find all callers of function")
    p.add_argument("--calls", type=str, help="find all calls to a function")
    p.add_argument("--calltree", type=str, help="show calltree starting from a function")
    # p.add_argument("-v", "--verbosity", type=int, choices=[0,1,2], default=0,
    #                help="increase output verbosity")
                   
    # group1 = p.add_mutually_exclusive_group(required=True)
    # group1.add_argument('--enable',action="store_true")
    # group1.add_argument('--disable',action="store_false")

    return(p.parse_args())
	
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
		print c["_source"]['c_function'][0]['name'], line_no(c)

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

def _calltree(line, depth):
	if depth == 0:
		return
	callers = find_callers(line)
	for c in callers:
		print ("  " * depth), line_no(c)
		_calltree(c, depth - 1)

def calltree(qualname, depth):
	edges = build_call_tree(qualname)
	for e in edges:
		print line_no(e[0]), line_no(e[1])

	# line = find_line_by_qualname(qualname)
	# _calltree(line, depth)


if __name__ == '__main__':
	TRACE = True

	args = vars(cmdline_args())

	# Dispatch to different commands.
	if args["callers"]:
		callers(args["callers"])

	if args["calls"]:
		calls(args["calls"], github_links=True)

	if args["calltree"]:
		calltree(args["calltree"], 4)


