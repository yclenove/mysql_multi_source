# coding: utf-8

"""MySQL 8.0.23+ 复制语法适配模块。

MySQL 8.0.23 起废弃了旧复制语法（CHANGE MASTER TO / START SLAVE 等），
改为 CHANGE REPLICATION SOURCE TO / START REPLICA 等新语法。
本模块封装版本检测和语法选择逻辑，使主文件中所有复制 SQL 调用统一走适配函数。

旧语法（< 8.0.23）          新语法（>= 8.0.23）
CHANGE MASTER TO             CHANGE REPLICATION SOURCE TO
START SLAVE                  START REPLICA
STOP SLAVE                   STOP REPLICA
SHOW SLAVE STATUS            SHOW REPLICA STATUS
RESET SLAVE                  RESET REPLICA
"""

import re
import logging

logger = logging.getLogger("mms.replication_syntax")

# 新语法切换的最低版本
_NEW_SYNTAX_CUTOFF = (8, 0, 23)


def mysql_version_tuple(version_str):
    """将 MySQL 版本字符串解析为 (major, minor, patch) 元组。

    Args:
        version_str: 如 "8.0.23", "5.7.35-log", "8.4.0"

    Returns:
        tuple: (major, minor, patch)，解析失败时返回 (0, 0, 0)
    """
    if not version_str:
        return (0, 0, 0)
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", str(version_str))
    if not m:
        return (0, 0, 0)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def is_new_syntax(version):
    """判断指定版本是否应使用新复制语法。

    Args:
        version: (major, minor, patch) 元组

    Returns:
        bool: True 表示使用新语法（>= 8.0.23）
    """
    return tuple(version) >= _NEW_SYNTAX_CUTOFF


def replication_sql(cmd, version, channel=None, host=None, port=None,
                    user=None, pwd=None):
    """根据 MySQL 版本返回正确的复制 SQL 语句。

    Args:
        cmd: 命令类型，支持 "CHANGE_MASTER" | "START" | "STOP" |
             "SHOW_STATUS" | "RESET"
        version: (major, minor, patch) 元组
        channel: 通道名称（START/STOP/SHOW_STATUS/RESET 必需，
                 CHANGE_MASTER 也需要）
        host: 主库主机（仅 CHANGE_MASTER 需要）
        port: 主库端口（仅 CHANGE_MASTER 需要）
        user: 复制用户名（仅 CHANGE_MASTER 需要）
        pwd: 复制密码（仅 CHANGE_MASTER 需要）

    Returns:
        str: 完整的 SQL 语句

    Raises:
        ValueError: 未知的 cmd 类型
    """
    new = is_new_syntax(version)

    if cmd == "CHANGE_MASTER":
        if new:
            sql = (
                "CHANGE REPLICATION SOURCE TO SOURCE_HOST='{host}', "
                "SOURCE_PORT={port}, SOURCE_USER='{user}', "
                "SOURCE_PASSWORD='{pwd}', SOURCE_AUTO_POSITION=1 "
                "FOR CHANNEL '{channel}'"
            )
        else:
            sql = (
                "CHANGE MASTER TO MASTER_HOST='{host}', MASTER_PORT={port}, "
                "MASTER_USER='{user}', MASTER_PASSWORD='{pwd}', "
                "MASTER_AUTO_POSITION=1 FOR CHANNEL '{channel}'"
            )
        return sql.format(
            host=host, port=port, user=user, pwd=pwd, channel=channel
        )

    elif cmd == "START":
        keyword = "REPLICA" if new else "SLAVE"
        return "START {} FOR CHANNEL '{}'".format(keyword, channel)

    elif cmd == "STOP":
        keyword = "REPLICA" if new else "SLAVE"
        return "STOP {} FOR CHANNEL '{}'".format(keyword, channel)

    elif cmd == "SHOW_STATUS":
        keyword = "REPLICA" if new else "SLAVE"
        return "SHOW {} STATUS FOR CHANNEL '{}'".format(keyword, channel)

    elif cmd == "RESET":
        keyword = "REPLICA" if new else "SLAVE"
        return "RESET {} FOR CHANNEL '{}'".format(keyword, channel)

    else:
        raise ValueError("未知的复制命令类型: {}".format(cmd))


class ReplicationSyntaxMixin(object):
    """复制语法适配 Mixin，提供实例级版本缓存和便捷方法。"""

    def _get_mysql_version(self):
        """获取并缓存 MySQL 版本元组（实例级缓存，每次请求检测一次）。

        Returns:
            tuple: (major, minor, patch)
        """
        cached = getattr(self, "_mysql_version_cache", None)
        if cached is not None:
            return cached
        try:
            rows = self._query_sql("SELECT VERSION() as v") or []
            if rows:
                raw = rows[0]
                ver_str = (raw.get("v") if isinstance(raw, dict) else raw[0]) or ""
                version = mysql_version_tuple(ver_str)
                self._mysql_version_cache = version
                return version
        except Exception as ex:
            logger.warning("获取 MySQL 版本失败，使用旧语法: %s", ex)
        fallback = (0, 0, 0)
        self._mysql_version_cache = fallback
        return fallback

    def _replication_sql(self, cmd, channel=None, host=None, port=None,
                         user=None, pwd=None):
        """便捷方法：根据当前 MySQL 版本返回正确的复制 SQL。

        Args:
            cmd: 命令类型
            channel: 通道名称
            host: 主库主机（仅 CHANGE_MASTER）
            port: 主库端口（仅 CHANGE_MASTER）
            user: 复制用户名（仅 CHANGE_MASTER）
            pwd: 复制密码（仅 CHANGE_MASTER）

        Returns:
            str: 完整的 SQL 语句
        """
        version = self._get_mysql_version()
        return replication_sql(
            cmd, version, channel=channel, host=host, port=port,
            user=user, pwd=pwd
        )
