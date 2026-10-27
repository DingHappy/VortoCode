export function SettingsReadFeedback({ label, loading, error, onRetry }: {
  label: string; loading: boolean; error: string; onRetry: () => void;
}) {
  if (!loading && !error) return null;
  return <div className="settings-read-feedback" role={error ? "alert" : "status"}>
    <div><strong>{loading ? `正在读取${label}…` : `无法读取${label}`}</strong>
      <p>{loading ? "读取完成后即可操作。" : `${error}。原配置未改动，请重试后再编辑。`}</p></div>
    {!loading && <button onClick={onRetry}>重试读取{label}</button>}
  </div>;
}
