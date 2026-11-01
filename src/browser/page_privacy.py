"""DOM projection for the managed browser. Never return form values.

Recognized login/secret forms pause projection before title,
body, URL or labels are read. This is a conservative DOM guard, not a promise to
detect every QR login or arbitrary secret rendered as ordinary page text.
"""

_HELPERS = r"""
const visible = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el);
  return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
const sensitive = el => {
  const type = (el.getAttribute('type') || '').toLowerCase();
  const autocomplete = (el.getAttribute('autocomplete') || '').toLowerCase();
  const name = ((el.getAttribute('name') || '') + ' ' + (el.getAttribute('id') || '')).toLowerCase();
  return type === 'password' || /(?:current-password|new-password|one-time-code)/.test(autocomplete)
    || /(?:password|passwd|passcode|(?:^|[\s_-])otp(?:$|[\s_-])|verification.?code|security.?code|api.?key|access.?token|refresh.?token|client.?secret)/.test(name);
};
const protectedPage = () => [...document.querySelectorAll('input,textarea')].some(el => visible(el) && sensitive(el));
const safeUrl = () => { try { const u = new URL(location.href); return u.origin + '/'; } catch { return ''; } };
const label = el => (el.getAttribute('aria-label') || el.innerText || el.getAttribute('placeholder')
  || el.getAttribute('title') || el.getAttribute('alt') || '').trim().replace(/\s+/g, ' ').slice(0,80);
"""

AUTH_STATE_JS = "(() => {" + _HELPERS + "return { protected: protectedPage(), frames: [...document.querySelectorAll('iframe,frame')].some(visible) }; })()"

SNAPSHOT_JS = "(() => {" + _HELPERS + r"""
if (protectedPage()) return { needs_login: true };
const items = []; let n = 0;
for (const el of document.querySelectorAll('a[href],button,input:not([type=hidden]),textarea,select,[role=button],[role=link],[contenteditable=true]')) {
  if (!visible(el) || n >= 150) continue;
  n += 1; el.setAttribute('data-vorto-ref', String(n));
  const tag = el.tagName.toLowerCase(), type = el.getAttribute('type') || '';
  items.push(`[${n}] <${tag}${type ? ' type=' + type : ''}> ${label(el)}`);
}
const text = (document.body ? document.body.innerText : '').replace(/\n{3,}/g,'\n\n').slice(0,6000);
return { title: document.title, url: safeUrl(), text, items };
})()"""

ELEMENT_JS = "((ref) => {" + _HELPERS + r"""
if (protectedPage()) return { sensitive: true };
const el = document.querySelector(`[data-vorto-ref="${ref}"]`);
if (!el) return null;
if (sensitive(el)) return { sensitive: true };
el.scrollIntoView({block:'center',inline:'center'});
const r=el.getBoundingClientRect();
return { x:r.left+r.width/2, y:r.top+r.height/2, tag:el.tagName.toLowerCase(),
  type:(el.getAttribute('type')||'').toLowerCase(), label:label(el),
  editable:el.isContentEditable || ['input','textarea'].includes(el.tagName.toLowerCase()) };
})(%s)"""
