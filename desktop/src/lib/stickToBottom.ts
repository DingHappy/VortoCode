/** 滚动容器里要用到的那几样；测试里用假对象代替真 DOM。 */
export interface ScrollBox {
  scrollTop: number;
  readonly scrollHeight: number;
  readonly children: ArrayLike<object>;
}

interface Observers {
  resize: new (callback: () => void) => { observe(target: object): void; disconnect(): void };
  mutation: new (callback: () => void) => { observe(target: object, options: { childList: boolean }): void; disconnect(): void };
}

/**
 * 内容变高时贴着底部（只在 shouldStick 为真时，也就是人本来就在底部、没在定位某一轮）。
 * 按 messages 变化滚一次不够：重连后历史先到、Codex 动作卡和决策等随后才加载出来，
 * 把内容撑高，视野就停在中间的旧内容上（真机 2026-10-29 场景 C：重连后不滚到底）。
 * 返回解除监听的函数。
 */
export function stickToBottom(node: ScrollBox, shouldStick: () => boolean, observers?: Observers): () => void {
  const kinds = observers ?? (typeof ResizeObserver === "undefined" || typeof MutationObserver === "undefined"
    ? null : { resize: ResizeObserver, mutation: MutationObserver } as unknown as Observers);
  if (!kinds) return () => undefined;
  let height = node.scrollHeight;
  const follow = () => {
    if (node.scrollHeight === height) return;
    height = node.scrollHeight;
    if (shouldStick()) node.scrollTop = height;
  };
  const resize = new kinds.resize(follow);
  // 子元素各自长高（卡片加载、代码高亮、图片）不改变容器本身的尺寸，要盯每个直接子元素
  const watch = () => {
    resize.disconnect();
    resize.observe(node);
    for (const child of Array.from(node.children)) resize.observe(child);
  };
  const mutation = new kinds.mutation(() => { watch(); follow(); });
  watch();
  mutation.observe(node, { childList: true });
  // 容器重新挂上（从小组、资料库等页面切回对话）时滚动位置从顶部开始；人原本就在底部，就先回到底部，
  // 否则只等内容再变高才跟，视野会停在最早的消息上（真机 2026-11-05：从小组切回主要聊天停在 10 月的旧消息）。
  if (shouldStick()) node.scrollTop = node.scrollHeight;
  return () => { resize.disconnect(); mutation.disconnect(); };
}
