"use client";

import { ChevronDown } from "lucide-react";
import { createContext, useContext, type AriaAttributes, type ComponentProps, type InputHTMLAttributes, type ReactNode } from "react";

import { cn } from "./cn";

interface FieldState {
  id: string;
  describedBy?: string;
  invalid: boolean;
}

const FieldContext = createContext<FieldState | null>(null);

/** Resolves id, aria-describedby and aria-invalid for a control inside a Field. */
function useFieldProps<T extends { id?: string; "aria-describedby"?: string; "aria-invalid"?: AriaAttributes["aria-invalid"] }>(
  props: T,
): T {
  const field = useContext(FieldContext);
  if (!field) return props;
  const describedBy = [props["aria-describedby"], field.describedBy].filter(Boolean).join(" ") || undefined;
  return {
    ...props,
    id: props.id ?? field.id,
    "aria-describedby": describedBy,
    "aria-invalid": props["aria-invalid"] ?? (field.invalid || undefined),
  };
}

export function Field({
  label,
  hint,
  error,
  children,
  htmlFor,
  className,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: string | null;
  children: ReactNode;
  htmlFor: string;
  className?: string;
}) {
  const hintId = hint ? `${htmlFor}-hint` : undefined;
  const errorId = error ? `${htmlFor}-error` : undefined;
  const describedBy = [errorId, hintId].filter(Boolean).join(" ") || undefined;
  return (
    <div className={cn("min-w-0", className)}>
      <label htmlFor={htmlFor} className="block text-sm font-medium text-ink">
        {label}
      </label>
      <FieldContext.Provider value={{ id: htmlFor, describedBy, invalid: Boolean(error) }}>
        <div className="mt-1.5">{children}</div>
      </FieldContext.Provider>
      {error ? (
        <p id={errorId} className="mt-1.5 text-xs text-bad">
          {error}
        </p>
      ) : null}
      {hint ? (
        <p id={hintId} className="mt-1.5 text-xs text-muted">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export const controlClasses =
  "block w-full rounded-md border border-line-strong/70 bg-surface px-2.5 text-sm text-ink transition-colors placeholder:text-subtle hover:border-line-strong focus-visible:border-accent focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-accent disabled:cursor-not-allowed disabled:bg-sunken disabled:text-muted aria-invalid:border-bad";

export function TextInput({ className, ...rest }: ComponentProps<"input">) {
  const props = useFieldProps(rest);
  const file = props.type === "file";
  return (
    <input
      {...props}
      className={cn(
        controlClasses,
        file
          ? "h-auto py-1.5 file:mr-3 file:rounded file:border-0 file:bg-sunken file:px-2.5 file:py-1 file:text-sm file:font-medium file:text-ink hover:file:bg-line"
          : "h-9",
        className,
      )}
    />
  );
}

export function TextArea({ className, ...rest }: ComponentProps<"textarea">) {
  const props = useFieldProps(rest);
  return <textarea {...props} className={cn(controlClasses, "min-h-20 py-2 leading-5", className)} />;
}

export function Select({ className, ...rest }: ComponentProps<"select">) {
  const props = useFieldProps(rest);
  if (props.multiple) {
    return <select {...props} className={cn(controlClasses, "py-1", className)} />;
  }
  return (
    <div className={cn("relative min-w-0", className)}>
      <select {...props} className={cn(controlClasses, "h-9 appearance-none pr-8")} />
      <ChevronDown aria-hidden="true" className="pointer-events-none absolute top-1/2 right-2.5 size-4 -translate-y-1/2 text-muted" />
    </div>
  );
}

/** A checkbox or radio with its label to the right and an optional description. */
export function ChoiceField({
  type = "checkbox",
  label,
  description,
  className,
  ...props
}: Omit<InputHTMLAttributes<HTMLInputElement>, "type"> & {
  type?: "checkbox" | "radio";
  label: ReactNode;
  description?: ReactNode;
}) {
  return (
    <label className={cn("flex min-w-0 cursor-pointer items-start gap-2.5 text-sm has-disabled:cursor-not-allowed has-disabled:opacity-60", className)}>
      <input type={type} {...props} className="mt-0.5 shrink-0" />
      <span className="min-w-0 break-words">
        <span className="text-ink">{label}</span>
        {description ? <span className="mt-0.5 block text-xs text-muted">{description}</span> : null}
      </span>
    </label>
  );
}

/** A labelled group of controls (fieldset + legend). */
export function FieldGroup({
  legend,
  hint,
  children,
  className,
  legendClassName,
}: {
  legend: ReactNode;
  hint?: ReactNode;
  children: ReactNode;
  className?: string;
  legendClassName?: string;
}) {
  return (
    <fieldset className={cn("min-w-0", className)}>
      <legend className={cn("text-sm font-medium text-ink", legendClassName)}>{legend}</legend>
      {hint ? <p className="mt-0.5 text-xs text-muted">{hint}</p> : null}
      <div className="mt-2">{children}</div>
    </fieldset>
  );
}

/** Inline form error shown above the submit row. */
export function FormError({ message }: { message: string | null | undefined }) {
  return (
    <div aria-live="assertive" aria-atomic="true">
      {message ? (
        <p role="alert" className="rounded-md border border-bad-line bg-bad-soft px-3 py-2 text-sm text-bad">
          {message}
        </p>
      ) : null}
    </div>
  );
}
