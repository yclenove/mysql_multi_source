#!/usr/bin/python
# coding: utf-8

import sys

panel_path = "/www/server/panel/class"
if panel_path not in sys.path:
    sys.path.append(panel_path)

import PluginLoader


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: btpython start_sync.py <method> [<task_id_or_source_id>] [<worker_id>]")
        sys.exit(1)

    method = sys.argv[1]

    # tick: no extra args needed (cron entry point)
    if method == "tick":
        PluginLoader.plugin_run("mysql_multi_source", "tick", {})
        sys.exit(0)

    if len(sys.argv) < 3:
        print("Usage: btpython start_sync.py <method> <task_id_or_source_id> [<worker_id>]")
        sys.exit(1)

    arg_value = sys.argv[2]
    payload = {"source_id": arg_value}
    if "task" in method:
        payload = {"task_id": arg_value}
    if len(sys.argv) >= 4:
        payload["worker_id"] = sys.argv[3]
    PluginLoader.plugin_run("mysql_multi_source", method, payload)
