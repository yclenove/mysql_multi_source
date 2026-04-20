#!/usr/bin/python
# coding: utf-8

import sys

panel_path = "/www/server/panel/class"
if panel_path not in sys.path:
    sys.path.append(panel_path)

import PluginLoader


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: btpython start_sync.py <method> <source_id>")
        sys.exit(1)

    method = sys.argv[1]
    source_id = sys.argv[2]
    PluginLoader.plugin_run("mysql_multi_source", method, {"source_id": source_id})
