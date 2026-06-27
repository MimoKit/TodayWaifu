from __future__ import annotations

from typing import Dict

from gsuid_core.utils.plugins_config.models import (
    GSC,
    GsBoolConfig,
    GsDivider,
    GsStrConfig,
)

CONFIG_DEFAULT: Dict[str, GSC] = {
    'DailyWifeImageSource': GsStrConfig(
        '图片数据源',
        '选择 local 使用本地 XWUID 图片目录；选择 gallery 使用远程图库接口。图库图片可能存在内容风险，请自行决定是否启用；使用风险自行承担，插件作者不承担责任',
        'local',
        options=['local', 'gallery'],
    ),
    'DailyWifeCustomRolePilePath': GsStrConfig(
        '本地角色图片目录',
        '图片数据源为 local 时生效。留空时自动查找 gsuid_core/data/XutheringWavesUID/custom_role_pile',
        '',
    ),
    'DailyWifeRoleMapPath': GsStrConfig(
        '角色 ID 对照表路径',
        '留空时使用插件内置 wife_role_id_map.txt',
        '',
    ),
    'DailyWifeGalleryApiUrl': GsStrConfig(
        '图库接口地址',
        'XWUID 图库角色立绘接口地址，默认使用 https://img.xlinxc.cn/api/xwuid/roles。启用图库即表示已知晓图片内容风险并自行承担',
        'https://img.xlinxc.cn/api/xwuid/roles',
    ),
    'DailyWifeGalleryUsername': GsStrConfig(
        '图库账号',
        '访问 XWUID 图库接口和图片所需的账号。图库内容可能存在风险，请自行决定是否使用',
        '',
        secret=True,
    ),
    'DailyWifeGalleryPassword': GsStrConfig(
        '图库密码',
        '访问 XWUID 图库接口和图片所需的密码。图库使用风险自行承担，插件作者不承担责任',
        '',
        secret=True,
    ),
    '_DividerLolicon': GsDivider('今日萝莉', ''),
    'DailyWifeLoliconCustomUrl': GsStrConfig(
        '今日萝莉接口地址',
        '【免责声明】第三方 API，作者不对其内容负责，使用风险自行承担。'
        '填入 GET 直接返回图片内容的接口地址，留空则禁用今日萝莉功能',
        '',
    ),
    'DailyWifeSendText': GsBoolConfig(
        '发送文字说明',
        '开启后图片前附带"你今天的老婆是xxx"',
        True,
    ),
    'DailyWifeReplyPrefixEnabled': GsBoolConfig(
        '启用回复前缀',
        '开启后插件回复会自动添加"[今日老婆]"前缀',
        True,
    ),
    'DailyWifeAtUser': GsBoolConfig(
        '发送时艾特触发者',
        '开启后发送今日老婆结果时会艾特触发者',
        True,
    ),
    'DailyWifeTextTemplate': GsStrConfig(
        '文字模板',
        '可用变量：{name} 角色名，{role_id} 数字 ID',
        '你今天的老婆是{name}',
    ),
    'DailyWifeEnableGroupMember': GsBoolConfig(
        '今日老婆概率抽群友',
        '开启后「今日老婆」会按配置概率从本群 GSCore 成员缓存里抽取群友，未命中或获取失败时仍抽鸣潮角色',
        False,
    ),
    'DailyWifeGroupMemberProbability': GsStrConfig(
        '今日老婆抽群友概率',
        '0 到 1 之间的小数，例如 0.1 表示 10% 概率抽群友；仅在开启今日老婆概率抽群友后生效',
        '0.1',
    ),
    'DailyWifeGroupMemberTextTemplate': GsStrConfig(
        '今日老婆抽群友文字模板',
        '今日老婆命中群友时的文字说明模板，可用变量：{name} 群友昵称，{user_id} 群友 QQ',
        '你今天的老婆是{name}',
    ),
    'DailyWifeMarryGroupMemberEnabled': GsBoolConfig(
        '启用娶群友',
        '开启后可使用「娶群友」命令，从本群 GSCore 成员缓存里抽取群友',
        False,
    ),
    'DailyWifeMarryGroupMemberTextTemplate': GsStrConfig(
        '娶群友文字模板',
        '「娶群友」命令的文字说明模板，可用变量：{name} 群友昵称，{user_id} 群友 QQ',
        '你娶到的群友是{name}',
    ),
    'DailyWifeHusbandEnabled': GsBoolConfig(
        '启用今日老公',
        '开启后可使用「今日老公」命令，只抽取男角色；关闭后命令不生效',
        False,
    ),
    'DailyHusbandTextTemplate': GsStrConfig(
        '今日老公文字模板',
        '今日老公的文字说明模板，可用变量：{name} 角色名，{role_id} 数字 ID',
        '你今天的老公是{name}',
    ),
    'DailyWifeRobEnabled': GsBoolConfig(
        '启用抢老婆',
        '开启后可以使用"抢老婆 @对方"抢对方当天老婆',
        True,
    ),
    'DailyWifeRobSuccessRate': GsStrConfig(
        '抢老婆成功率',
        '0 到 1 之间的小数，例如 0.5 表示 50%',
        '0.5',
    ),
    'DailyWifeRobSuccessTemplate': GsStrConfig(
        '抢老婆成功文案',
        '可用变量：{name} 角色名，{role_id} 数字 ID，{target} 被抢用户 ID',
        '抢老婆成功！你把对方今天的老婆{name}抢过来了！',
    ),
    'DailyWifeGiftEnabled': GsBoolConfig(
        '启用送老婆',
        '开启后可以使用"送老婆 @对方"，对方发送"同意送老婆"后完成赠送',
        True,
    ),
    'DailyWifeGiftSuccessTemplate': GsStrConfig(
        '送老婆成功文案',
        '可用变量：{name} 角色名，{role_id} 数字 ID，{target} 接收用户 ID',
        '你把今天的老婆{name}送给了对方！',
    ),
}
