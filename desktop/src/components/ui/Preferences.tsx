import { useId, type ReactNode } from "react";
import { ChevronDown, Info } from "lucide-react";
import "./Preferences.css";

/** Low-frequency explanations remain readable by click, keyboard and touch. */
export function PreferenceDetails({ label, children, className = "" }: {
  label: ReactNode; children: ReactNode; className?: string;
}) {
  return <details className={`vc-preference-details ${className}`}>
    <summary><Info aria-hidden="true" /><span>{label}</span><ChevronDown aria-hidden="true" /></summary>
    <div className="vc-preference-details-body">{children}</div>
  </details>;
}

/** Shared setting rhythm; the caller owns persistence, confirmation and errors. */
export function PreferenceRow({ title, htmlFor, description, status, children }: {
  title: ReactNode; htmlFor?: string; description?: ReactNode; status?: ReactNode; children?: ReactNode;
}) {
  const id = useId();
  return <div className="vc-preference-row">
    <div className="vc-preference-copy">
      {htmlFor ? <label htmlFor={htmlFor}>{title}</label> : <strong>{title}</strong>}
      {description && <span className="vc-preference-description">{description}</span>}
      {status && <span id={id} className="vc-preference-status" role="status">{status}</span>}
    </div>
    {children && <div className="vc-preference-control" aria-describedby={status ? id : undefined}>{children}</div>}
  </div>;
}

export function PreferenceGroup({ title, description, children }: {
  title: ReactNode; description?: ReactNode; children: ReactNode;
}) {
  const id = useId();
  return <section className="vc-preference-group" aria-labelledby={id}>
    <header><h3 id={id}>{title}</h3>{description && <p>{description}</p>}</header>
    <div className="vc-preference-list">{children}</div>
  </section>;
}
