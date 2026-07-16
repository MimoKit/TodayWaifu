from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DailyKindMetadata:
    bucket: str
    title: str
    role_mode: str
    text_template_key: str
    text_template_default: str
    rob_enabled_key: str
    rob_success_rate_key: str
    rob_success_key: str
    rob_success_default: str
    gift_enabled_key: str
    gift_success_key: str
    gift_success_default: str


DAILY_KIND_METADATA = {
    "wife": DailyKindMetadata(
        bucket="wives",
        title="老婆",
        role_mode="wife",
        text_template_key="DailyWifeTextTemplate",
        text_template_default="你今天的老婆是{name}",
        rob_enabled_key="DailyWifeRobEnabled",
        rob_success_rate_key="DailyWifeRobSuccessRate",
        rob_success_key="DailyWifeRobSuccessTemplate",
        rob_success_default="抢老婆成功！你把对方今天的老婆{name}抢过来了！",
        gift_enabled_key="DailyWifeGiftEnabled",
        gift_success_key="DailyWifeGiftSuccessTemplate",
        gift_success_default="你把今天的老婆{name}送给了对方！",
    ),
    "husband": DailyKindMetadata(
        bucket="husbands",
        title="老公",
        role_mode="husband",
        text_template_key="DailyHusbandTextTemplate",
        text_template_default="你今天的老公是{name}",
        rob_enabled_key="DailyHusbandRobEnabled",
        rob_success_rate_key="DailyWifeRobSuccessRate",
        rob_success_key="DailyHusbandRobSuccessTemplate",
        rob_success_default="抢老公成功！你把对方今天的老公{name}抢过来了！",
        gift_enabled_key="DailyHusbandGiftEnabled",
        gift_success_key="DailyHusbandGiftSuccessTemplate",
        gift_success_default="你把今天的老公{name}送给了对方！",
    ),
    "nte": DailyKindMetadata(
        bucket="nte_wives",
        title="异环老婆",
        role_mode="nte",
        text_template_key="DailyWifeNteTextTemplate",
        text_template_default="你今天的异环老婆是{name}。",
        rob_enabled_key="",
        rob_success_rate_key="",
        rob_success_key="",
        rob_success_default="",
        gift_enabled_key="",
        gift_success_key="",
        gift_success_default="",
    ),
    "loli": DailyKindMetadata(
        bucket="lolis",
        title="萝莉",
        role_mode="wife",
        text_template_key="",
        text_template_default="",
        rob_enabled_key="DailyLoliRobEnabled",
        rob_success_rate_key="DailyLoliRobSuccessRate",
        rob_success_key="DailyLoliRobSuccessTemplate",
        rob_success_default="抢萝莉成功！你把对方今天的萝莉抢过来了！",
        gift_enabled_key="DailyLoliGiftEnabled",
        gift_success_key="DailyLoliGiftSuccessTemplate",
        gift_success_default="你把今天的萝莉送给了对方！",
    ),
}


def daily_kind_metadata(kind: str) -> DailyKindMetadata:
    return DAILY_KIND_METADATA.get(kind, DAILY_KIND_METADATA["wife"])
