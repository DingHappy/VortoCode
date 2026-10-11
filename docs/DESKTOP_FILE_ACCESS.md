# Desktop 本机文件读取合同

版本：0.1.6；入口：在当前对话给出完整文件路径，不必先选项目。

设置 → 授权级别：每次确认会显示规范文件路径，允许一次只用于本次读取；只读免确认允许
干净回合读取普通文本，不保存按目录的永久授权。档位跟随当前工作区，General单独保存。
读过不可信资料后重新确认。系统文件权限不能由应用授权绕过。

工具 `read_local_file({"path":"/absolute/report.txt"})` 仅由Desktop原装配入口提供。
成功：`ok,path,content,bytes_read,truncated,limit_bytes=65536,authority=read_only`。
失败：`ok=false,error`，不含content；取消继续传播。truncated不能等同已读全文。
审批期间更新、删除、替换、软链重定向及读取期间观察到版本变化均需新请求；旧批准不跟随新文件。
原与规范路径中的敏感目录/凭据/内部状态不读取；普通UTF-8之外拒绝。只读取单个普通文件，
不扫描目录，不展开~，不写入，不执行，不授予敏感文件/联网凭据/进程能力。

原 `/api/trust` GET/PUT在Desktop General可用；新增可选投影`local_file_reads=true`说明能力，
原level/effective/ceiling语义不变，General最高reads。普通Web General仍拒绝该REST入口且无本机工具。
`permissions.yaml`的原deny可按`read_local_file: /absolute/pattern*`阻止派发；allow不新增免确认合同。

这是按授权读取的第一步。跨目录修改、目录级授权、失效期限/撤销/授权清单和敏感文件批准
尚未实现，不能把完全信任档位当成无边界文件权限。原任务与覆盖证据合同保持。

## 0.1.7 确认卡与监管

确认卡可保存“只读免确认”，不再需要转到设置；本次仍需点允许，保存失败不批准。
新版Desktop默认监管：只提供ask/reads；历史full保存记录保留，effective=reads，不能
免写入、执行和外发审批。GET /api/trust新增可选supervised=true，仅表示新版Desktop
原装配的此监管合同，capability_profile与权限审批上限分开，原local profile不再意味免确认。

所有Desktop shell命令（包括只读）仍问人；原已识别危险命令硬拒绝。已识别敏感参数的
项目文件工具与本机只读入口硬拦截，原项目路径围栏、deny与沙箱保留。不是完整风险识别器，
不覆盖任意脚本或第三方工具隐含副作用；提高档位不能取消这些闸门，不授予新的provider权限。
非Desktop CLI/Web旧合同不改，旧runtime的确认卡不得伪装新监管。
