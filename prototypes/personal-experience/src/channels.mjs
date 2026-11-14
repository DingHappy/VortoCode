// Synthetic channel records for the design preview, never imported by the runtime.
export const PREVIEW_CHANNELS = [
  {
    id: "daily",
    name: "日常",
    topic: "日常安排、想法与需要继续跟进的事",
    project: "个人空间",
  },
  {
    id: "research",
    name: "资料研究",
    topic: "保留问题、来源和结论，让研究可以接着往下做",
    project: "个人空间",
  },
  {
    id: "development",
    name: "项目开发",
    topic: "围绕一个已登记项目讨论实现、验证与交付",
    project: "VortoOne · 已登记项目",
  },
];

export const CHANNEL_MESSAGES = {
  "这台 Mac:个人空间:channel:daily": [
    {
      role: "user",
      text: "零散想法先放在这里，等周末一起整理。",
      time: "08:40",
    },
    {
      role: "assistant",
      text: "好。这个频道用来接着讨论日常事项；某个话题展开时，可以在那条消息下回复。",
      time: "08:40",
    },
  ],
  "这台 Mac:个人空间:channel:research": [
    {
      role: "user",
      text: "这个频道专门跟进个人助理产品。先研究安装引导和频道是怎么组织的。",
      time: "09:02",
    },
    {
      role: "assistant",
      text: "这次先看三件事：如何开始使用、话题如何持续、结果如何回查。具体疑问可以在这条消息下继续讨论。",
      time: "09:03",
      progress: true,
    },
    {
      role: "assistant",
      kind: "research",
      label: "交付回传",
      text: "公开资料已整理。安装引导与日常界面分层；频道承接持续话题，单个问题可以在回复里展开。",
      time: "09:12",
      document: true,
    },
    {
      role: "user",
      text: "先继续研究频道这一部分，安装引导放到下一步。",
      time: "09:15",
    },
  ],
};

export const CHANNEL_REPLIES = {
  "这台 Mac:个人空间:channel:research:1": [
    {
      role: "user",
      text: "频道和普通对话的区别，主要体现在哪里？",
      time: "09:08",
    },
    {
      role: "assistant",
      text: "频道有明确主题，后续消息与交付继续留在同一个地方；某条消息的细节可以在回复里讨论。",
      time: "09:09",
    },
  ],
};
