from __future__ import annotations

from typing import Dict

from gsuid_core.data_store import get_res_path
from gsuid_core.utils.plugins_config.models import (
    GSC,
    GsBoolConfig,
    GsDivider,
    GsImageConfig,
    GsIntConfig,
    GsStrConfig,
)

SHOW_CONFIG_PATH = get_res_path(['TodayWaifu', 'show'])

CONFIG_DEFAULT: Dict[str, GSC] = {
    '_DividerImageSource': GsDivider('图片数据源', ''),
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
        '图库角色立绘接口地址，默认使用 https://img.xlinxc.cn/api/xwuid/roles。启用图库即表示已知晓图片内容风险并自行承担',
        'https://img.xlinxc.cn/api/xwuid/roles',
    ),
    'DailyWifeLoliApiUrl': GsStrConfig(
        '今日萝莉接口地址',
        'GET 后直接返回图片内容的接口地址，留空则改用本地萝莉图库',
        'https://loli.xlinxc.cn',
    ),
    '_DividerNsfwCheck': GsDivider('NSFW 检测', ''),
    'DailyWifeNsfwCheckEnabled': GsBoolConfig(
        '启用 NSFW 检测',
        '开启后发送今日老婆/老公图片前会调用检测服务；未通过的图片会静默重抽，不直接发出。默认关闭',
        False,
    ),
    'DailyWifeNsfwCheckUrl': GsStrConfig(
        'NSFW 检测地址',
        '检测服务地址。可填服务根地址，也可填完整 /check/upload；如需鉴权可按检测服务要求配置查询参数',
        '',
        secret=True,
    ),

    '_DividerBasicReply': GsDivider('基础回复设置', ''),
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
    'DailyWifeShowRoleId': GsBoolConfig(
        '显示角色 ID',
        '开启后文字说明会额外附带一行"角色ID：xxx"',
        False,
    ),
    'DailyWifeDebugMode': GsBoolConfig(
        '主人 Debug 模式',
        '开启后机器人主人可以用"今日老婆 角色名"指定抽取的角色，便于调试',
        False,
    ),

    '_DividerDailyWife': GsDivider('今日老婆', ''),
    'DailyWifeTextTemplate': GsStrConfig(
        '今日老婆文字模板',
        '今日老婆的文字说明模板，可用变量：{name} 角色名，{role_id} 数字 ID',
        '你今天的老婆是{name}。',
    ),

    '_DividerGroupMember': GsDivider('群友玩法', ''),
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
        '你今天抽到的群友是{name}。',
    ),
    'DailyWifeMarryGroupMemberEnabled': GsBoolConfig(
        '启用娶群友',
        '开启后可使用「娶群友」命令，从本群 GSCore 成员缓存里抽取群友',
        False,
    ),
    'DailyWifeMarryGroupMemberTextTemplate': GsStrConfig(
        '娶群友文字模板',
        '「娶群友」命令的文字说明模板，可用变量：{name} 群友昵称，{user_id} 群友 QQ',
        '你娶到的群友是{name}。',
    ),

    '_DividerDailyHusband': GsDivider('今日老公', ''),
    'DailyWifeHusbandEnabled': GsBoolConfig(
        '启用今日老公',
        '开启后可使用「今日老公」命令，只抽取男角色；关闭后命令不生效',
        False,
    ),
    'DailyHusbandTextTemplate': GsStrConfig(
        '今日老公文字模板',
        '今日老公的文字说明模板，可用变量：{name} 角色名，{role_id} 数字 ID',
        '你今天的老公是{name}。',
    ),

    '_DividerRob': GsDivider('抢夺设置', ''),
    'DailyWifeRobEnabled': GsBoolConfig(
        '启用抢老婆',
        '开启后可以使用"抢老婆 @对方"抢对方当天老婆',
        True,
    ),
    'DailyWifeRobSuccessRate': GsStrConfig(
        '抢老婆/老公成功率',
        '0 到 1 之间的小数，例如 0.5 表示 50%；抢老公也复用这个成功率',
        '0.5',
    ),
    'DailyWifeRobSuccessTemplate': GsStrConfig(
        '抢老婆成功文案',
        '可用变量：{name} 角色名，{role_id} 数字 ID，{target} 被抢用户 ID',
        '得手了！{name}现在跟你走了。',
    ),
    'DailyHusbandRobEnabled': GsBoolConfig(
        '启用抢老公',
        '开启后可以使用"抢老公 @对方"抢对方当天老公',
        True,
    ),
    'DailyHusbandRobSuccessTemplate': GsStrConfig(
        '抢老公成功文案',
        '可用变量：{name} 角色名，{role_id} 数字 ID，{target} 被抢用户 ID',
        '得手了！{name}现在跟你走了。',
    ),
    'DailyLoliRobEnabled': GsBoolConfig(
        '启用抢萝莉',
        '开启后可以使用"抢萝莉 @对方"抢对方当天萝莉',
        True,
    ),
    'DailyLoliRobSuccessRate': GsStrConfig(
        '抢萝莉成功率',
        '0 到 1 之间的小数，例如 0.5 表示 50%',
        '0.5',
    ),
    'DailyLoliRobSuccessTemplate': GsStrConfig(
        '抢萝莉成功文案',
        '可用变量：{name} 名称，{role_id} 图片标识，{target} 被抢用户 ID',
        '得手了！对方今天的萝莉现在归你了。',
    ),

    '_DividerGift': GsDivider('赠送设置', ''),
    'DailyWifeGiftEnabled': GsBoolConfig(
        '启用送老婆',
        '开启后可以使用“送老婆 @对方”，对方发送“接受老婆赠送”后完成赠送',
        True,
    ),
    'DailyWifeGiftSuccessTemplate': GsStrConfig(
        '送老婆成功文案',
        '可用变量：{name} 角色名，{role_id} 数字 ID，{target} 接收用户 ID',
        '赠送成功，{name}已经送到对方手里。',
    ),
    'DailyHusbandGiftEnabled': GsBoolConfig(
        '启用送老公',
        '开启后可以使用“送老公 @对方”，对方发送“接受老公赠送”后完成赠送',
        True,
    ),
    'DailyHusbandGiftSuccessTemplate': GsStrConfig(
        '送老公成功文案',
        '可用变量：{name} 角色名，{role_id} 数字 ID，{target} 接收用户 ID',
        '赠送成功，{name}已经送到对方手里。',
    ),
    'DailyLoliGiftEnabled': GsBoolConfig(
        '启用送萝莉',
        '开启后可以使用“送萝莉 @对方”，对方发送“接受萝莉赠送”后完成赠送',
        True,
    ),
    'DailyLoliGiftSuccessTemplate': GsStrConfig(
        '送萝莉成功文案',
        '可用变量：{name} 名称，{role_id} 图片标识，{target} 接收用户 ID',
        '赠送成功，今天的萝莉已经送到对方手里。',
    ),
}

APPEARANCE_CONFIG_DEFAULT: Dict[str, GSC] = {
    'DailyWifeHelpBannerBgUpload': GsImageConfig(
        '帮助横幅图',
        '自定义「今日老婆帮助」顶部横幅图，留空或文件不存在时使用插件默认横幅',
        str(SHOW_CONFIG_PATH / 'help_banner.png'),
        str(SHOW_CONFIG_PATH),
        'help_banner',
        'png',
    ),
    'DailyWifeHelpBgUpload': GsImageConfig(
        '帮助背景图',
        '自定义「今日老婆帮助」整体背景图，留空或文件不存在时使用插件默认背景',
        str(SHOW_CONFIG_PATH / 'help_bg.png'),
        str(SHOW_CONFIG_PATH),
        'help_bg',
        'png',
    ),
    'DailyWifeHelpIconUpload': GsImageConfig(
        '帮助头像',
        '自定义「今日老婆帮助」左上角头像，建议使用方形图片',
        str(SHOW_CONFIG_PATH / 'help_icon.png'),
        str(SHOW_CONFIG_PATH),
        'help_icon',
        'png',
    ),
    'DailyWifeHelpColumn': GsIntConfig(
        '帮助展示行数',
        '控制帮助图每组展示数量，默认 3，可按需要改成 4、5 等',
        3,
        10,
    ),
}
