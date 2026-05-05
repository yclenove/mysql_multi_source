# coding: utf-8

import re


class ValidatorsMixin(object):
    def _validate_channel_name(self, channel_name):
        return re.match(r"^[A-Za-z0-9_]{1,64}$", channel_name or "") is not None

    def _validate_source_id(self, source_id):
        return re.match(r"^[A-Za-z0-9_\-]{1,64}$", source_id or "") is not None

    def _validate_mysql_scope_name(self, value):
        return re.match(r"^[A-Za-z0-9_\-$]+$", value or "") is not None

    def _validate_privileges_text(self, value):
        return re.match(r"^[A-Za-z_,\s]+$", value or "") is not None

    def _validate_gtid_set(self, value):
        """校验 GTID 集合格式，防止 SQL 注入。

        MySQL GTID 格式: UUID:interval[,UUID:interval...]
        合法示例:
          3E11FA47-71CA-11E1-9E33-C80AA9429562:1-5
          3E11FA47-71CA-11E1-9E33-C80AA9429562:1-5:6-10
          3E11FA47-71CA-11E1-9E33-C80AA9429562:1-5,2C6B1A2F-71CA-11E1-9E33-C80AA9429562:1-3
        """
        uuid_seg = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
        gtid_unit = uuid_seg + r":\d+(?:-\d+)*(?::\d+(?:-\d+)*)*"
        pattern = r"^{u}(?:,{u})*$".format(u=gtid_unit)
        return re.match(pattern, str(value or "").strip()) is not None
