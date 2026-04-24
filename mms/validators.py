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
