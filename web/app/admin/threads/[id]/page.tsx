import { redirect } from "next/navigation";

export default async function AdminThreadCompat({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  redirect(`/inbox/${encodeURIComponent(id)}`);
}
