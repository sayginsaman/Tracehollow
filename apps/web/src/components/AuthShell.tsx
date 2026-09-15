export function AuthShell({
  title,
  description,
  children,
}: {
  title: string;
  description: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <main id="main" className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-4 py-12">
      <p className="mb-6 text-sm font-semibold tracking-wide text-muted">Tracehollow</p>
      <div className="rounded-lg border border-line bg-surface p-6 shadow-sm">
        <h1 className="text-xl font-semibold">{title}</h1>
        <div className="mt-2 text-sm text-muted">{description}</div>
        <div className="mt-6">{children}</div>
      </div>
    </main>
  );
}
