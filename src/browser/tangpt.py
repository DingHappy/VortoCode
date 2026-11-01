"""Read-only TangPT header observation; never login, navigate or grant rights.

The known header structure and signed label were observed on 2026-11-01.
Unsigned labels are conservative UI hints, not proof of today's server state.
"""
import re

from src.browser.page_privacy import _HELPERS

ORIGIN = "https://www.tangpt.top"

STATUS_JS = "(() => {" + _HELPERS + r"""
if (protectedPage()) return { protected: true };
const origin = location.origin;
const empty = { version: 1, origin, protected: false, accounts: [], attendance: [] };
if (origin !== 'https://www.tangpt.top') return empty;
const matches = (el, path) => {
  try { const u = new URL(el.getAttribute('href'), location.href);
    return u.origin === origin && u.pathname === path; } catch { return false; }
};
const exits = [...document.querySelectorAll('a[href*="logout.php"]')].filter(el => visible(el) && matches(el, '/logout.php'));
if (exits.length !== 1) return empty;
const header = exits[0].closest('td');
if (!header) return empty;
const links = header.querySelectorAll('a[href]');
if (links.length > 128) return empty;
const accounts = [], attendance = [];
for (const el of links) {
  if (!visible(el)) continue;
  if (matches(el, '/userdetails.php')) {
    const u = new URL(el.getAttribute('href'), location.href), ids = u.searchParams.getAll('id');
    const name = (el.innerText || '').trim().replace(/\s+/g, ' ');
    accounts.push({ id: ids.length === 1 ? ids[0] : '', name: name.length <= 80 ? name : '' });
  }
  if (matches(el, '/attendance.php')) {
    const text = (el.innerText || '').trim().replace(/\s+/g, ' ');
    attendance.push(text.length <= 128 ? text : '');
  }
}
return { ...empty, accounts, attendance };
})()"""

_SIGNED = re.compile(r"\[签到已得\s*\d{1,12}(?:\.\d{1,4})?\s*[,，]\s*补签卡\s*[:：]\s*\d{1,8}\]")
_OFFERED = frozenset({"签到", "[签到]", "每日签到", "[每日签到]", "点击签到", "[点击签到]"})


def project_status(raw):
    """Only exact visible evidence produces a state; absent/conflicting stays unknown."""
    result = {"site": ORIGIN, "source": "visible_header", "login": "unknown", "account": None,
              "account_verified": False, "attendance": "unknown", "evidence": "",
              "site_date": None, "business_verified": False,
              "next_step": "暂停提交，人工核对登录、实际账号和签到状态。"}
    if (not isinstance(raw, dict) or set(raw) != {"version", "origin", "protected", "accounts", "attendance"}
            or type(raw["version"]) is not int or raw["version"] != 1 or raw["origin"] != ORIGIN
            or raw["protected"] is not False or not isinstance(raw["accounts"], list)
            or len(raw["accounts"]) != 1):
        return result
    account = raw["accounts"][0]
    if (not isinstance(account, dict) or set(account) != {"id", "name"}
            or not isinstance(account["id"], str) or not re.fullmatch(r"[1-9][0-9]{0,19}", account["id"])
            or not isinstance(account["name"], str) or not 1 <= len(account["name"].strip()) <= 80
            or any(ord(c) < 32 or ord(c) == 127 for c in account["name"])):
        return result
    result.update(login="observed_logged_in", account={"id": account["id"], "name": account["name"].strip()})
    labels = raw["attendance"]
    if not isinstance(labels, list) or len(labels) != 1 or not isinstance(labels[0], str) or len(labels[0]) > 128:
        return result
    text = labels[0].strip()
    if _SIGNED.fullmatch(text):
        result.update(attendance="page_reports_signed", evidence=text,
                      next_step="报告页面已显示签到奖励，不再点击签到入口；站点日期和实际账号仍须核对。")
    elif text in _OFFERED:
        result.update(attendance="page_offers_checkin", evidence=text,
                      next_step="页面提供签到入口；尚未证明今日未签到。核对实际账号、站点日期后再单独请求操作确认。")
    return result
