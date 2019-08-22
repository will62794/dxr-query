#!/bin/sh
#
# Produce a DOT call graph starting at a given line.
#
./elastic.py --dotcalltreeline $1 | dot -Tsvg -ocallgraph.svg && open callgraph.svg

