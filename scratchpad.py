import pprint

from elasticsearch import Elasticsearch                                                                                                                                                                                                       
es = Elasticsearch(["10.1.2.40:9200"])
mongo_master_index = 'dxr_19_mongodb_8f516504-c320-11e9-94a3-0cc47ad955d7'

filename = "service_entry_point_common.cpp"
base_line = 658

# Do a query.
doc = {
	"size": 100,
	"query": {
	    "bool": {
	      "must": [
	        {
	          "exists": {
	            "field": "c_call"
	          }
	        },
	        {
	          "match": {
	            "file_name": filename
	          }
	        }
	      ]
	    }
	}
}

# Find enclosing function for this line number.

mongo_master_index = 'dxr_19_mongodb_8f516504-c320-11e9-94a3-0cc47ad955d7'
res = es.search(index=mongo_master_index, body=doc,scroll='1m')
print "Took %dms" % res["took"]
hits = res["hits"]["hits"]
print "Found %d hits for query %s" % (len(hits), str(doc))
# pprint.pprint(hits)
# for el in res["hits"]["hits"][0]["_source"].keys():
# 	print el
# print res["hits"]["hits"][0]["_source"]["c_member"]

line_nums = []
for h in hits:
	pprint.pprint(h["_source"]["c_call"])
	# pprint.pprint(h["_source"]["number"])
	# line_nums.append(h["_source"]["number"][0])
print line_nums

# Find the greatest line number less than the base_line i.e. the first function definition
# above the target line.
# fn_line = max(filter(lambda ln : ln < base_line, line_nums))


# Query again for the function def line.
doc = {
	"size": 100,
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
	            "file_name": filename
	          }
	        }
	      ]
	    }
	}
}


# print fn_line
# res = es.search(index=mongo_master_index, body=doc,scroll='1m')
# print "Took %dms" % res["took"]
# hits = res["hits"]["hits"]
# print "Found %d hits for query %s" % (len(hits), str(doc))
# pprint.pprint(hits[0])
