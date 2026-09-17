"use client";

import { LoaderCircle, type LucideIcon } from "lucide-react";
import Link from "next/link";
import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ComponentProps, ReactNode } from "react";

import { cn } from "./cn";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "danger-ghost";
export type ButtonSize = "md" | "sm";

const BASE =
  "inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-md font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-55 aria-disabled:cursor-not-allowed aria-disabled:opacity-55";

const VARIANTS: Record<ButtonVariant, string> = {
  primary: "bg-accent text-on-accent hover:bg-accent-strong active:bg-accent-strong disabled:hover:bg-accent",
  secondary: "border border-line-strong/60 bg-surface text-ink hover:bg-sunken active:bg-line/60 disabled:hover:bg-surface",
  ghost: "text-muted hover:bg-sunken hover:text-ink active:bg-line/60 disabled:hover:bg-transparent",
  danger: "bg-bad text-on-accent hover:opacity-90 active:opacity-85 disabled:hover:opacity-55",
  "danger-ghost": "text-bad hover:bg-bad-soft active:bg-bad-soft disabled:hover:bg-transparent",
};

const SIZES: Record<ButtonSize, string> = {
  md: "h-9 px-3.5 text-sm",
  sm: "h-8 px-2.5 text-sm",
};

export function buttonClasses(variant: ButtonVariant = "secondary", size: ButtonSize = "md", className?: string): string {
  return cn(BASE, VARIANTS[variant], SIZES[size], className);
}

export function Button({
  variant = "secondary",
  size = "md",
  icon: Icon,
  busy = false,
  className,
  children,
  ...props
}: ComponentProps<"button"> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: LucideIcon;
  /** Shows a spinner in place of the icon and sets aria-busy; the label stays. */
  busy?: boolean;
}) {
  return (
    <button
      type="button"
      {...props}
      aria-busy={busy || props["aria-busy"] ? true : undefined}
      className={buttonClasses(variant, size, className)}
    >
      {busy ? (
        <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
      ) : Icon ? (
        <Icon aria-hidden="true" className="size-4" />
      ) : null}
      {children}
    </button>
  );
}

export function ButtonLink({
  href,
  variant = "secondary",
  size = "md",
  icon: Icon,
  className,
  children,
  external = false,
  ...props
}: AnchorHTMLAttributes<HTMLAnchorElement> & {
  href: string;
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: LucideIcon;
  /** Plain anchor (downloads, API URLs) instead of client-side navigation. */
  external?: boolean;
  children: ReactNode;
}) {
  const classes = buttonClasses(variant, size, className);
  const content = (
    <>
      {Icon ? <Icon aria-hidden="true" className="size-4" /> : null}
      {children}
    </>
  );
  if (external) {
    return (
      <a href={href} {...props} className={classes}>
        {content}
      </a>
    );
  }
  return (
    <Link href={href} {...props} className={classes}>
      {content}
    </Link>
  );
}

/** An icon-only button; the label is its accessible name and tooltip. */
export function IconButton({
  icon: Icon,
  label,
  variant = "ghost",
  size = "sm",
  className,
  ...props
}: Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> & {
  icon: LucideIcon;
  label: string;
  variant?: ButtonVariant;
  size?: ButtonSize;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      {...props}
      className={cn(BASE, VARIANTS[variant], size === "sm" ? "size-8" : "size-9", className)}
    >
      <Icon aria-hidden="true" className="size-4" />
    </button>
  );
}
