# coding: utf-8

import public


class DiagnoseServiceMixin(object):
    def _classify_error(self, err_msg):
        err = str(err_msg or "").lower()
        if "access denied" in err or "permission" in err:
            return "权限问题"
        if "connection" in err or "timed out" in err or "network" in err:
            return "网络问题"
        if "gtid" in err:
            return "GTID问题"
        if "duplicate" in err or "conflict" in err:
            return "数据冲突"
        if "space" in err or "disk" in err or "memory" in err:
            return "资源不足"
        return "未知问题"

    def _classify_connectivity_error(self, err_msg):
        err = str(err_msg or "").lower()
        if "timed out" in err:
            return "网络超时"
        if "refused" in err:
            return "端口拒绝"
        if "no route" in err or "unreachable" in err:
            return "路由不可达"
        if "access denied" in err:
            return "账号或权限错误"
        return "未知连接错误"

    def diagnose_source(self, get):
        if not hasattr(get, "source_id"):
            return public.returnMsg(False, "missing parameter: source_id")
        data = self._load_config()
        source = self._find_source(data, str(get.source_id).strip())
        if not source:
            return public.returnMsg(False, "source not found")

        status = self._get_source_status(source.get("channel_name"))
        network_ok = self.test_source_connection(get).get("status", False)
        gtid_info = self.get_gtid_status().get("msg", {})
        suggestions = []
        if not network_ok:
            suggestions.append("检查主库网络连通性和防火墙白名单")
        if not gtid_info.get("enabled"):
            suggestions.append("当前从库 GTID 未开启，建议开启后再使用 GTID 自动定位")
        if status.get("last_error"):
            suggestions.append("根据 Last_Error 先处理权限/位点/数据冲突问题")
        if not source.get("db_mappings"):
            suggestions.append("建议配置库映射，避免多源同名库冲突")
        if not suggestions:
            suggestions.append("当前状态正常，可继续观察复制延迟")

        return public.returnMsg(
            True,
            {
                "source_id": source.get("source_id"),
                "channel_name": source.get("channel_name"),
                "status": status,
                "network_ok": network_ok,
                "gtid_enabled": gtid_info.get("enabled", False),
                "suggestions": suggestions,
            },
        )

    def wizard_diagnose_all(self, get=None):
        """Classify issues across sources and tasks into actionable groups."""
        data = self._load_config()
        groups = {
            "network": [], "auth": [], "gtid": [],
            "conflict": [], "resource": [], "config": [], "other": [],
        }

        def bucket(kind_cn):
            m = {
                "网络问题": "network", "网络超时": "network", "端口拒绝": "network", "路由不可达": "network",
                "权限问题": "auth", "账号或权限错误": "auth",
                "GTID问题": "gtid",
                "数据冲突": "conflict",
                "资源不足": "resource",
                "未知连接错误": "network",
                "未知问题": "other",
            }
            return m.get(kind_cn, "other")

        status_map = self._all_slave_status()
        for src in data.get("sources", []) or []:
            row = status_map.get(src.get("channel_name") or "", None)
            st = self._map_status_row(row or {})
            if st.get("last_error"):
                kind = self._classify_error(st["last_error"])
                groups[bucket(kind)].append({
                    "source_id": src.get("source_id"),
                    "channel_name": src.get("channel_name"),
                    "message": st["last_error"],
                    "category": kind,
                    "fixable": kind in ("网络问题", "权限问题", "GTID问题"),
                })

        for t in (data.get("bootstrap_tasks", []) or [])[:50]:
            if t.get("status") == "failed" and t.get("error"):
                kind = t.get("error_type") or self._classify_error(t.get("error"))
                groups[bucket(kind)].append({
                    "task_id": t.get("task_id"),
                    "source_id": t.get("source_id"),
                    "message": t.get("error"),
                    "category": kind,
                    "fixable": kind in ("权限问题", "资源不足", "网络问题"),
                })

        try:
            mh = self.master_health_check().get("msg", {})
            for item in mh.get("items", []):
                if item.get("status") == "fail":
                    groups["config"].append({
                        "name": item.get("name"),
                        "current": item.get("current"),
                        "expected": item.get("expected"),
                        "message": item.get("suggestion"),
                        "category": "配置",
                        "fixable": True,
                    })
        except Exception:
            pass

        total = sum(len(v) for v in groups.values())
        return self._ok(
            {"groups": groups, "total_issues": total},
            "诊断完成",
            "DIAGNOSE_OK",
        )

    def wizard_quick_fix(self, get):
        """Best-effort one-click fix for selected category."""
        if not hasattr(get, "category"):
            return self._fail("缺少参数: category", "ERR_PARAM_REQUIRED")
        cat = str(get.category).strip().lower()
        if cat in ("config", "gtid"):
            return self.master_auto_fix_apply(get)
        if cat == "stuck_tasks" or cat == "task":
            return self.recover_bootstrap_tasks()
        if cat in ("network", "auth"):
            return self._ok(
                {"hint": "网络/账号问题需检查云安全组、主库防火墙与复制账号 user@host 限制。"},
                "已输出自助排查指引",
                "FIX_HINT",
            )
        return self._fail("不支持的修复类别: {}".format(cat), "ERR_UNKNOWN_CATEGORY")
