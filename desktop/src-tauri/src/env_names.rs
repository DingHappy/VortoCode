//! 环境变量从 `VORTOCODE_*` 改名为 `VORTOONE_*`（ADR-018 批次 B），与引擎 `src/env_compat.py` 同一口径：
//! 读的时候先新名字、再旧名字；起引擎时把继承来的旧名字换成新名字，旧名字不再往下传。

use std::ffi::OsString;
use std::process::Command;

const NEW_PREFIX: &str = "VORTOONE_";
const LEGACY_PREFIX: &str = "VORTOCODE_";

fn legacy_name(name: &str) -> Option<String> {
    name.strip_prefix(NEW_PREFIX).map(|rest| format!("{LEGACY_PREFIX}{rest}"))
}

/// 读环境变量：先 `VORTOONE_X`，没有再读改名前的 `VORTOCODE_X`。
pub(crate) fn var(name: &str) -> Option<String> {
    std::env::var(name)
        .ok()
        .or_else(|| legacy_name(name).and_then(|legacy| std::env::var(legacy).ok()))
}

/// 子进程会继承的旧名字：值挪到新名字下（父进程两个都有时以新名字为准），旧名字从子进程环境里删掉。
///
/// 必须在按偏好设置/清除具体开关**之前**调用：之后的 `env` / `env_remove` 只管新名字就够了，
/// 不会出现「清掉了 VORTOONE_X、继承来的 VORTOCODE_X 又被引擎读回来」。
pub(crate) fn adopt_legacy_env(
    command: &mut Command,
    inherited: impl IntoIterator<Item = (OsString, OsString)>,
) {
    let inherited: Vec<(OsString, OsString)> = inherited.into_iter().collect();
    for (key, value) in &inherited {
        let Some(rest) = key.to_str().and_then(|key| key.strip_prefix(LEGACY_PREFIX)) else {
            continue;
        };
        command.env_remove(key);
        let name = format!("{NEW_PREFIX}{rest}");
        if !inherited.iter().any(|(other, _)| other.to_str() == Some(name.as_str())) {
            command.env(name, value);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsStr;

    fn env_of<'a>(command: &'a Command, key: &str) -> Option<Option<&'a OsStr>> {
        command.get_envs().find(|(name, _)| *name == OsStr::new(key)).map(|(_, value)| value)
    }

    fn inherited(pairs: &[(&str, &str)]) -> Vec<(OsString, OsString)> {
        pairs.iter().map(|(k, v)| (OsString::from(k), OsString::from(v))).collect()
    }

    #[test]
    fn legacy_name_moves_to_new_name_and_is_not_passed_on() {
        let mut command = Command::new("fixture");
        adopt_legacy_env(&mut command, inherited(&[("VORTOCODE_IM", "dingtalk"), ("PATH", "/bin")]));
        assert_eq!(env_of(&command, "VORTOONE_IM"), Some(Some(OsStr::new("dingtalk"))));
        assert_eq!(env_of(&command, "VORTOCODE_IM"), Some(None));
        assert_eq!(env_of(&command, "PATH"), None);
    }

    #[test]
    fn new_name_inherited_from_parent_wins() {
        let mut command = Command::new("fixture");
        adopt_legacy_env(&mut command, inherited(&[("VORTOCODE_CRON", "1"), ("VORTOONE_CRON", "0")]));
        assert_eq!(env_of(&command, "VORTOONE_CRON"), None); // 不动，子进程照常继承父进程的新名字
        assert_eq!(env_of(&command, "VORTOCODE_CRON"), Some(None));
    }

    #[test]
    fn a_switch_cleared_afterwards_stays_cleared() {
        // 父进程里只有旧名字的桌面操作开关；偏好没开，configure_desktop_control 只清新名字。
        let mut command = Command::new("fixture");
        adopt_legacy_env(&mut command, inherited(&[("VORTOCODE_ENABLE_DESKTOP_CONTROL", "1")]));
        command.env_remove("VORTOONE_ENABLE_DESKTOP_CONTROL");
        assert_eq!(env_of(&command, "VORTOONE_ENABLE_DESKTOP_CONTROL"), Some(None));
        assert_eq!(env_of(&command, "VORTOCODE_ENABLE_DESKTOP_CONTROL"), Some(None));
    }

    #[test]
    fn legacy_name_maps_only_new_prefixed_names() {
        assert_eq!(legacy_name("VORTOONE_ACCOUNT_URL").as_deref(), Some("VORTOCODE_ACCOUNT_URL"));
        assert_eq!(legacy_name("HOME"), None);
    }
}
