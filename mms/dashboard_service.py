# coding: utf-8

import os

import public


class DashboardServiceMixin(object):
    def source_detail(self, get):
        if not hasattr(get, "source_id"):
            return self._fail("缺少参数: source_id", "ERR_PARAM_REQUIRED")
        data = self._load_config()
        item = self._find_source(data, str(get.source_id).strip())
        if not item:
            return self._fail("未找到该数据源", "ERR_NOT_FOUND")
        result = dict(item)
        plain = self._crypto_decrypt(result.get("repl_password", ""))
        result["repl_password"] = self._mask_secret(plain)
        result["has_password"] = bool(plain)
        return public.returnMsg(True, result)

    def get_source_logs(self, get):
        if not hasattr(get, "source_id"):
            return public.returnMsg(False, "missing parameter: source_id")
        source_id = str(get.source_id).strip()
        log_path = os.path.join(self.log_dir, "{}.log".format(source_id))
        if not os.path.exists(log_path):
            return public.returnMsg(True, "")
        content = public.ReadFile(log_path) or ""
        keyword = str(get.keyword).strip() if hasattr(get, "keyword") else ""
        if keyword:
            filtered = []
            for line in content.splitlines():
                if keyword in line:
                    filtered.append(line)
            content = "\n".join(filtered)
        return public.returnMsg(True, content or "")

    def get_task_logs(self, get):
        if not hasattr(get, "task_id"):
            return public.returnMsg(False, "missing parameter: task_id")
        task_id = str(get.task_id).strip()
        log_path = os.path.join(self.log_dir, "task_{}.log".format(task_id))
        if not os.path.exists(log_path):
            return public.returnMsg(True, "")
        content = public.ReadFile(log_path) or ""
        keyword = str(get.keyword).strip() if hasattr(get, "keyword") else ""
        if keyword:
            filtered = []
            for line in content.splitlines():
                if keyword in line:
                    filtered.append(line)
            content = "\n".join(filtered)
        return public.returnMsg(True, content or "")

    def overview_metrics(self, get=None):
        data = self._load_config()
        total = len(data.get("sources", []))
        running = 0
        stopped = 0
        errors = 0
        status_map = self._all_slave_status()
        for source in data.get("sources", []):
            status = self._map_status_row(status_map.get(source.get("channel_name") or "", {}) or {})
            if status.get("running"):
                running += 1
            else:
                stopped += 1
            if status.get("last_error"):
                errors += 1
        tasks = data.get("bootstrap_tasks", [])
        done_tasks = [t for t in tasks if t.get("status") == "done"]
        failed_tasks = [t for t in tasks if t.get("status") == "failed"]
        avg_duration = 0
        if done_tasks:
            avg_duration = int(sum([int(t.get("duration_seconds", 0) or 0) for t in done_tasks]) / len(done_tasks))
        return public.returnMsg(
            True,
            {
                "total_sources": total,
                "running_sources": running,
                "stopped_sources": stopped,
                "error_sources": errors,
                "bootstrap_tasks": len(tasks),
                "bootstrap_done": len(done_tasks),
                "bootstrap_failed": len(failed_tasks),
                "avg_bootstrap_duration_seconds": avg_duration,
            },
        )

    def _all_slave_status(self):
        """Fetch SHOW REPLICA STATUS once and return {channel_name: row}."""
        out = {}
        try:
            sql = self._replication_sql("SHOW_STATUS_ALL")
            rows = self._query_sql(sql)
        except Exception:
            return out
        if not isinstance(rows, (list, tuple)) or not rows:
            return out
        for row in rows:
            if isinstance(row, dict):
                name = row.get("Channel_Name") or row.get("channel_name") or ""
                out[str(name)] = row
        return out

    def _map_status_row(self, row):
        if not isinstance(row, dict):
            return {
                "running": False, "io_running": "No", "sql_running": "No",
                "seconds_behind": None, "last_error": "",
            }
        io_running = row.get("Slave_IO_Running", row.get("Replica_IO_Running", "No"))
        sql_running = row.get("Slave_SQL_Running", row.get("Replica_SQL_Running", "No"))
        seconds_behind = row.get("Seconds_Behind_Master", row.get("Seconds_Behind_Source"))
        last_error = row.get("Last_Error", "") or row.get("Last_IO_Error", "") or row.get("Last_SQL_Error", "")
        return {
            "running": io_running == "Yes" and sql_running == "Yes",
            "io_running": io_running,
            "sql_running": sql_running,
            "seconds_behind": seconds_behind,
            "last_error": last_error,
        }

    def wizard_dashboard_snapshot(self, get=None):
        """Return per-source status + running tasks with one SHOW REPLICA STATUS."""
        data = self._load_config()
        sources = data.get("sources", []) or []
        tasks = data.get("bootstrap_tasks", []) or []

        status_map = self._all_slave_status()
        source_rows = []
        running = 0
        stopped = 0
        errors = 0
        for src in sources:
            row = status_map.get(src.get("channel_name") or "", None)
            st = self._map_status_row(row or {})
            if st["running"]:
                running += 1
            else:
                stopped += 1
            if st.get("last_error"):
                errors += 1
            plain = self._crypto_decrypt(src.get("repl_password", ""))
            source_rows.append({
                "source_id": src.get("source_id"),
                "channel_name": src.get("channel_name"),
                "master_host": src.get("master_host"),
                "master_port": src.get("master_port"),
                "repl_user": src.get("repl_user"),
                "repl_password_masked": self._mask_secret(plain),
                "db_mappings": src.get("db_mappings", []),
                "status": st,
                "updated_at": src.get("updated_at"),
            })

        live_tasks = []
        for t in tasks[:50]:
            if t.get("status") in ("pending", "running"):
                live_tasks.append({
                    "task_id": t.get("task_id"),
                    "source_id": t.get("source_id"),
                    "mode": t.get("mode"),
                    "effective_mode": t.get("effective_mode"),
                    "status": t.get("status"),
                    "current_step": t.get("current_step"),
                    "progress": t.get("progress"),
                    "retry_count": t.get("retry_count"),
                    "error": t.get("error"),
                    "error_type": t.get("error_type"),
                    "started_at": t.get("started_at"),
                    "last_heartbeat": t.get("last_heartbeat"),
                })

        done_tasks = [t for t in tasks if t.get("status") == "done"]
        avg_duration = 0
        if done_tasks:
            avg_duration = int(sum(int(t.get("duration_seconds", 0) or 0) for t in done_tasks) / len(done_tasks))

        return self._ok(
            {
                "sources": source_rows,
                "live_tasks": live_tasks,
                "metrics": {
                    "total_sources": len(sources),
                    "running_sources": running,
                    "stopped_sources": stopped,
                    "error_sources": errors,
                    "bootstrap_tasks": len(tasks),
                    "bootstrap_done": len(done_tasks),
                    "bootstrap_failed": len([t for t in tasks if t.get("status") == "failed"]),
                    "avg_bootstrap_duration_seconds": avg_duration,
                },
                "mode": data.get("mode", "replica_mode"),
                "generated_at": self._now(),
            },
            "仪表盘快照已生成",
            "DASHBOARD_OK",
        )
