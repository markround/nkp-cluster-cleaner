#!/usr/bin/env bash

cat src/nkp_cluster_cleaner/main.py| grep envvar | cut -d\' -f2 |  sort | uniq | awk '{orig=$0; gsub(/_/, "-"); print "| --" tolower($0) " | " orig " | "}'
