#!/bin/bash
PATH=/www/server/panel/pyenv/bin:/bin:/sbin:/usr/bin:/usr/sbin:/usr/local/bin:/usr/local/sbin:~/bin
export PATH

plugin_path=/www/server/panel/plugin/mysql_multi_source

Install_MysqlMultiSource() {
    mkdir -p "${plugin_path}/log"
    if [ -f "${plugin_path}/icon.png" ]; then
        cp -f "${plugin_path}/icon.png" /www/server/panel/BTPanel/static/img/soft_ico/ico-mysql_multi_source.png
    fi
    echo "Successify"
}

Uninstall_MysqlMultiSource() {
    rm -rf "${plugin_path}"
    rm -f /www/server/panel/BTPanel/static/img/soft_ico/ico-mysql_multi_source.png
    echo "Successify"
}

if [ "${1}" == "install" ]; then
    Install_MysqlMultiSource
elif [ "${1}" == "update" ]; then
    Install_MysqlMultiSource
elif [ "${1}" == "uninstall" ]; then
    Uninstall_MysqlMultiSource
else
    echo "Usage: $0 {install|update|uninstall}"
    exit 1
fi
