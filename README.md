# Querying the DXR Code Search Database  

Examples:

Build DOT call graph of given function:
```
./elastic.py --calltree "mongo::RecordStore::insertRecords"
```

Find all calls to a given function:
```
./elastic.py --calls "mongo::RecordStore::insertRecords"
```

Find all callers of a given function:
```
./elastic.py --callers "mongo::RecordStore::insertRecords"
```
