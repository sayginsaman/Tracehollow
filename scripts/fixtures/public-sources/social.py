"""Synthetic stand-ins for the social platform interfaces, used only by scripts/verify-phase4.sh.

Shapes follow the documented formats (Instagram Graph API Business Discovery, Telegram Bot API,
YouTube Data API v3) and the observed layouts of the Telegram channel preview and Instagram
profile pages. Every account, channel, token and message is fictitious. Standard library only.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs

GRAPH_TOKEN = "EAAverificationGraphToken000000000000000000"
GRAPH_REJECTED = "EAAverificationRejectedToken00000000000000"
IG_USER_ID = "17841400000000001"
BOT_TOKEN = "123456789:AAverificationBotToken_00000000000000000"
YOUTUBE_KEY = "AIzaVerificationKey00000000000000000000000"

MEDIA_FIELDS = "id,caption,media_type,media_product_type,permalink,shortcode,timestamp,like_count,comments_count"

PREVIEW_HEAD = """<!doctype html><html><head><title>Örnek Haber</title>
<script>window.stolen = document.cookie</script></head><body>
<div class="tgme_channel_info">
 <div class="tgme_channel_info_header"><div class="tgme_channel_info_header_title"><span dir="auto">Örnek Haber</span></div>
 <div class="tgme_channel_info_header_username"><a href="https://t.me/ornekhaber">@ornekhaber</a></div></div>
 <div class="tgme_channel_info_description">Yerel haberler (synthetic)</div>
 <div class="tgme_channel_info_counters"><div class="tgme_channel_info_counter"><span class="counter_value">12.4K</span> <span class="counter_type">subscribers</span></div></div>
</div><section class="tgme_channel_history js-message_history">"""


def _post(number: int, text: str, when: str, *, edited: bool = False, media: str = "") -> str:
    meta = "edited " if edited else ""
    return f"""<div class="tgme_widget_message_wrap js-widget_message_wrap">
<div class="tgme_widget_message js-widget_message" data-post="ornekhaber/{number}"><div class="tgme_widget_message_bubble">
{media}<div class="tgme_widget_message_text js-message_text" dir="auto">{text}</div>
<div class="tgme_widget_message_footer"><div class="tgme_widget_message_info"><span class="tgme_widget_message_views">1.2K</span>
<span class="tgme_widget_message_meta">{meta}<a class="tgme_widget_message_date" href="https://t.me/ornekhaber/{number}"><time datetime="{when}" class="time">x</time></a></span>
</div></div></div></div></div>"""


PREVIEW_NEWEST = (
    PREVIEW_HEAD
    + '<div class="tgme_widget_message_centered js-messages_more_wrap"><a href="/s/ornekhaber?before=97" class="tme_messages_more js-messages_more" data-before="97"></a></div>'
    + _post(97, "İzmir şubesi açıldı. Ayrıntılar: <a href=\"https://ornek.example/haber\">ornek.example/haber</a>", "2026-09-10T07:30:00+00:00", media='<a class="tgme_widget_message_photo_wrap" href="#"></a>')
    + _post(100, "Düzeltme: açılış tarihi 12 Eylül.", "2026-09-11T09:00:00+00:00", edited=True)
    + "</section></body></html>"
)
PREVIEW_OLDER = (
    PREVIEW_HEAD
    + _post(95, "Kuruluş duyurusu.", "2026-09-01T09:00:00+00:00")
    + _post(96, "Basın toplantısı yarın.", "2026-09-05T09:00:00+00:00")
    + "</section></body></html>"
)

PROFILE_PAGE = """<!doctype html><html><head><title>Açık Profil (@acik.profil)</title>
<meta property="og:title" content="Açık Profil (@acik.profil) • Instagram photos and videos">
<meta property="og:description" content="2,048 Followers, 12 Following, 34 Posts - See Instagram photos and videos from Açık Profil (@acik.profil)">
<meta property="og:url" content="https://www.instagram.com/acik.profil/">
</head><body><script>fetch('https://tracker.example/x')</script></body></html>"""

LOGIN_PAGE = """<!doctype html><html><head><title>Login • Instagram</title></head><body>
<form action="/accounts/login/ajax/"><input name="username"><input name="password"></form></body></html>"""


def _graph(handler: Any, path: str, query: dict[str, list[str]]) -> None:
    token = (handler.headers.get("authorization") or "").removeprefix("Bearer ")
    if token == GRAPH_REJECTED:
        return handler._json(400, {"error": {"message": "Error validating access token: Session has expired", "type": "OAuthException", "code": 190, "error_subcode": 463}})
    if token != GRAPH_TOKEN or path != f"/graph/v25.0/{IG_USER_ID}":
        return handler._json(400, {"error": {"message": "Invalid OAuth access token.", "type": "OAuthException", "code": 190}})
    fields = query.get("fields", [""])[0]
    usage = {"x-app-usage": json.dumps({"call_count": 3, "total_time": 1, "total_cputime": 1})}
    if "username(kisisel.hesap)" in fields:
        return handler._json(400, {"error": {"message": "Invalid user id", "type": "OAuthException", "code": 110, "error_subcode": 2207013}}, usage)
    if "username(ornek.magaza)" not in fields:
        return handler._json(400, {"error": {"message": "Unsupported request", "code": 100}})
    media_first = [
        {"id": "m1", "caption": "Yeni ürün (synthetic)", "media_type": "IMAGE", "timestamp": "2026-09-01T10:00:00+0000", "like_count": 5, "comments_count": 1, "permalink": "https://www.instagram.com/p/m1/"},
        {"id": "m2", "caption": "Kampanya", "media_type": "VIDEO", "timestamp": "2026-08-20T08:30:00+0000", "comments_count": 0},
    ]
    if ".after(QVFIUmN1)" in fields:
        body: dict[str, Any] = {"business_discovery": {"id": "17841405555555555", "media": {"data": [{"id": "m3", "timestamp": "2026-07-01T00:00:00+0000"}]}}, "id": IG_USER_ID}
    else:
        body = {
            "business_discovery": {
                "id": "17841405555555555", "username": "ornek.magaza", "biography": "El yapımı ürünler (synthetic)",
                "website": "https://ornek.example", "followers_count": 1520, "media_count": 3,
                "media": {"data": media_first, "paging": {"cursors": {"before": "QVFIUmN0", "after": "QVFIUmN1"}}},
            },
            "id": IG_USER_ID,
        }  # fmt: skip
    return handler._json(200, body, usage)


def _instagram_web(handler: Any, path: str) -> None:
    if path == "/instagram-web/acik.profil/":
        return handler._send(200, PROFILE_PAGE)
    if path == "/instagram-web/ornek.magaza/":
        return handler._send(302, headers={"location": "/instagram-web/accounts/login/?next=/ornek.magaza/"})
    if path.startswith("/instagram-web/accounts/login/"):
        return handler._send(200, LOGIN_PAGE)
    return handler._send(404, "Sorry, this page isn't available.")


def _telegram_web(handler: Any, path: str, query: dict[str, list[str]]) -> None:
    if path == "/tme/s/ornekhaber":
        return handler._send(200, PREVIEW_OLDER if query.get("before") == ["97"] else PREVIEW_NEWEST)
    if path == "/tme/s/gizlikanal":
        return handler._send(302, headers={"location": "/tme/gizlikanal"})
    if path == "/tme/gizlikanal":
        return handler._send(200, "<html><body>Join channel</body></html>")
    return handler._send(404, "not found")


def _bot(handler: Any, path: str, query: dict[str, list[str]]) -> None:
    prefix = f"/telegram-bot/bot{BOT_TOKEN}/"
    if not path.startswith(prefix):
        return handler._json(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})
    method = path.removeprefix(prefix)
    chat = query.get("chat_id", [""])[0]
    if method == "getChat" and chat == "@ornekhaber":
        return handler._json(200, {"ok": True, "result": {"id": -1001234567890, "type": "channel", "title": "Örnek Haber", "username": "ornekhaber", "description": "Yerel haberler (synthetic)"}})
    if method == "getChatMemberCount" and chat == "-1001234567890":
        return handler._json(200, {"ok": True, "result": 12431})
    return handler._json(400, {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"})


def _youtube(handler: Any, path: str, query: dict[str, list[str]]) -> None:
    if handler.headers.get("x-goog-api-key") != YOUTUBE_KEY:
        return handler._json(400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.", "errors": [{"reason": "badRequest"}]}})
    if "key" in query:
        return handler._json(400, {"error": {"code": 400, "message": "the key must not be sent in the URL", "errors": [{"reason": "badRequest"}]}})
    if path == "/youtube/v3/channels":
        if query.get("forHandle") == ["@kotadolu"]:
            return handler._json(403, {"error": {"code": 403, "message": "quota exceeded", "errors": [{"reason": "quotaExceeded"}]}})
        if query.get("forHandle") == ["@ornekkanal"]:
            return handler._json(200, {"items": [{"id": "UCverification00000000000", "snippet": {"title": "Örnek Kanal", "customUrl": "@ornekkanal", "publishedAt": "2020-01-02T03:04:05Z"}, "contentDetails": {"relatedPlaylists": {"uploads": "UUverification00000000000"}}, "statistics": {"viewCount": "1000", "subscriberCount": "10", "videoCount": "2"}}]})
        return handler._json(200, {"items": []})
    if path == "/youtube/v3/playlistItems":
        return handler._json(200, {"items": [
            {"snippet": {"title": "Tanıtım", "position": 0, "channelId": "UCverification00000000000"}, "contentDetails": {"videoId": "abcdefghijk", "videoPublishedAt": "2026-09-01T00:00:00Z"}, "status": {"privacyStatus": "public"}},
            {"snippet": {"title": "Private video", "position": 1}, "contentDetails": {"videoId": "privatevid1"}, "status": {"privacyStatus": "private"}},
        ], "pageInfo": {"totalResults": 2}})  # fmt: skip
    if path == "/youtube/v3/videos":
        video = query.get("id", [""])[0]
        if video in ("abcdefghijk", "yorumkapali"):
            stats = {"viewCount": "10"} if video == "yorumkapali" else {"viewCount": "10", "commentCount": "1"}
            return handler._json(200, {"items": [{"id": video, "snippet": {"title": "Tanıtım", "channelId": "UCverification00000000000", "publishedAt": "2026-09-01T00:00:00Z"}, "statistics": stats, "status": {"privacyStatus": "public"}}]})
        return handler._json(200, {"items": []})
    if path == "/youtube/v3/commentThreads":
        if query.get("videoId") == ["yorumkapali"]:
            return handler._json(403, {"error": {"code": 403, "message": "The video has disabled comments.", "errors": [{"reason": "commentsDisabled", "domain": "youtube.commentThread"}]}})
        return handler._json(200, {"items": [{"id": "Ugverification1", "snippet": {"totalReplyCount": 0, "topLevelComment": {"id": "Ugverification1", "snippet": {"authorDisplayName": "@yorumcu", "authorChannelId": {"value": "UCauthor000000000000000"}, "textOriginal": "Harika video (synthetic)", "publishedAt": "2026-09-02T10:00:00Z", "updatedAt": "2026-09-02T10:00:00Z", "likeCount": 1}}}}]})
    return handler._json(404, {"error": {"code": 404, "errors": [{"reason": "notFound"}]}})


def handle(handler: Any, path: str, raw_query: str) -> bool:
    """Answer a social-platform fixture request; False when the path is not one of them."""
    query = parse_qs(raw_query)
    if path.startswith("/graph/"):
        _graph(handler, path, query)
    elif path.startswith("/instagram-web/"):
        _instagram_web(handler, path)
    elif path.startswith("/tme/"):
        _telegram_web(handler, path, query)
    elif path.startswith("/telegram-bot/"):
        _bot(handler, path, query)
    elif path.startswith("/youtube/"):
        _youtube(handler, path, query)
    else:
        return False
    return True
