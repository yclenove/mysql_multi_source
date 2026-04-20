#!/usr/bin/python
# coding: utf-8

import sys

panel_path = "/www/server/panel/class"
if panel_path not in sys.path:
    sys.path.append(panel_path)

import PluginLoader


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: btpython start_sync.py <method> <task_id_or_source_id>")
        sys.exit(1)

    method = sys.argv[1]
    arg_value = sys.argv[2]
    payload = {"source_id": arg_value}
    if "task" in method:
        payload = {"task_id": arg_value}
    PluginLoader.plugin_run("mysql_multi_source", method, payload)
