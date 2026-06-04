from __future__ import annotations

from typing import Dict

from gsuid_core.utils.plugins_config.models import (
    GSC,
    GsBoolConfig,
    GsFloatConfig,
    GsStrConfig,
)

CONFIG_DEFAULT: Dict[str, GSC] = {
    'DailyWifeCustomRolePilePath': GsStrConfig(
        '角色图片目录',
        '留空时自动查找 gsuid_core/data/XutheringWavesUID/custom_role_pile；也可以手动填写绝对路径',
        '',
    ),
    'DailyWifeRoleMapPath': GsStrConfig(
        '角色 ID 对照表路径',
        '留空时优先使用插件内置 role_id_map.txt；也可以手动填写自己的对照表路径',
        '',
    ),
    'DailyWifeSendText': GsBoolConfig(
        '发送文字说明',
        '开启后图片前附带“你今天的老婆是xxx”',
        True,
    ),
    'DailyWifeShowRoleId': GsBoolConfig(
        '显示角色 ID',
        '开启后在文字说明里额外显示本次角色对应的数字 ID',
        False,
    ),
    'DailyWifeTextTemplate': GsStrConfig(
        '文字模板',
        '可用变量：{name} 角色名，{role_id} 数字 ID',
        '你今天的老婆是{name}',
    ),
    'DailyWifeEnableGroupMember': GsBoolConfig(
        '启用群成员老婆',
        '开启后，群聊触发今日老婆时有概率抽到本群已记录成员；关闭后只抽鸣潮角色',
        False,
    ),
    'DailyWifeGroupMemberProbability': GsFloatConfig(
        '群成员抽取概率',
        '启用群成员老婆后生效，范围 0~1，例如 0.1 表示 10% 概率抽到群成员',
        0.1,
        0.0,
        1.0,
    ),
    'DailyWifeMasterUnlimited': GsBoolConfig(
        '主人无限抽老婆',
        '开启后，GSCore 主人触发今日老婆或娶群友时不再按每日固定结果，可重复随机抽取',
        True,
    ),
    'DailyWifeOneBotApiUrl': GsStrConfig(
        'OneBot HTTP API 地址',
        '用于直抓群成员列表，留空则只使用 GSCore 成员缓存；例如 http://127.0.0.1:3000',
        '',
    ),
    'DailyWifeOneBotAccessToken': GsStrConfig(
        'OneBot HTTP API Token',
        'OneBot HTTP API 的 access_token，没有则留空',
        '',
    ),
}