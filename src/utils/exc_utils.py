def _exc_text(e: BaseException) -> str:
    """把异常渲染成**人能据以定位**的一行。

    裸 `str(e)` 会丢掉最关键的类型：`KeyError('descriptions')` 只剩 `'descriptions'`，
    `TimeoutError()` 干脆是空串。于是用户看到「(任务分解出错: 'descriptions')」甚至
    「(出错: )」——无从下手。类型是定位的第一线索，永远带上。
    """
    detail = str(e).strip()
    return f"{type(e).__name__}: {detail}" if detail else type(e).__name__


def add_note(error: BaseException, note: str) -> None:
    """BaseException.add_note 是 3.11+；3.10 直接写 __notes__，读侧 getattr(e, "__notes__", ()) 两版一致。"""
    adder = getattr(error, "add_note", None)
    if adder is not None:
        adder(note)
    else:
        error.__notes__ = [*getattr(error, "__notes__", ()), note]  # type: ignore[attr-defined]
