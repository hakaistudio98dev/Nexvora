"use client";
import { useCallback, useEffect, useState } from "react";
import { useCan, useMe } from "@/components/me-context";
import { Alert, Badge, Button, Field, Input, Modal, PageHeader } from "@/components/ui";
import { api, errorText } from "@/lib/api";
import type { Page, Role, User } from "@/lib/types";

export default function UsersPage() {
  const me = useMe();
  const canWrite = useCan("user:write");
  const [users, setUsers] = useState<User[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      setUsers((await api.get<Page<User>>("/users?limit=200")).items);
      if (me.permissions.includes("role:read")) setRoles(await api.get<Role[]>("/roles"));
    } catch (e) { setErr(errorText(e)); }
  }, [me.permissions]);
  useEffect(() => { load(); }, [load]);

  async function toggle(u: User) {
    if (!confirm(u.is_active ? `Nonaktifkan ${u.email}? Semua sesinya langsung berakhir.` : `Aktifkan kembali ${u.email}?`)) return;
    try { await api.patch(`/users/${u.id}`, { is_active: !u.is_active }); load(); } catch (e) { setErr(errorText(e)); }
  }

  const assignable = roles.filter((r) => r.code !== "SUPER_ADMIN" || me.roles.includes("SUPER_ADMIN"));

  return (
    <>
      <PageHeader title="Tim & akses" desc="Setiap pengguna hanya melihat dan mengubah apa yang diizinkan perannya."
        action={canWrite && <Button onClick={() => setOpen(true)}>Undang anggota tim</Button>} />
      {err && <Alert>{err}</Alert>}
      <div className="overflow-x-auto rounded-xl border border-concrete-dark bg-white shadow-card">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-concrete-dark bg-concrete">
            <tr><th className="px-4 py-3">Nama</th><th className="px-4 py-3">Peran</th><th className="px-4 py-3">Login terakhir</th><th className="px-4 py-3">Status</th>{canWrite && <th className="px-4 py-3"><span className="sr-only">Aksi</span></th>}</tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className="border-b border-concrete-dark">
                <td className="px-4 py-3"><div className="font-semibold">{u.full_name}</div><div className="text-ink-muted">{u.email}</div></td>
                <td className="px-4 py-3"><div className="flex flex-wrap gap-1">{u.role_codes.map((r) => <Badge key={r}>{r}</Badge>)}</div></td>
                <td className="px-4 py-3 text-ink-soft">{u.last_login_at ? new Date(u.last_login_at).toLocaleString("id-ID") : "Belum pernah"}</td>
                <td className="px-4 py-3">{u.is_active ? <Badge tone="ok">Aktif</Badge> : <Badge tone="off">Nonaktif</Badge>}</td>
                {canWrite && (
                  <td className="px-4 py-3 text-right">
                    {u.id !== me.id && <Button variant="ghost" onClick={() => toggle(u)}>{u.is_active ? "Nonaktifkan" : "Aktifkan"}</Button>}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {roles.length > 0 && (
        <section className="mt-10">
          <h2 className="mb-3 text-lg font-bold">Peran dan izinnya</h2>
          <div className="grid gap-3 md:grid-cols-2">
            {roles.map((r) => (
              <div key={r.code} className="rounded-lg border border-concrete-line bg-white p-4">
                <div className="font-semibold">{r.name}</div>
                <p className="text-sm text-ink-soft">{r.description}</p>
                <p className="mt-2 font-mono text-xs text-ink-muted">{r.permissions.join("  ")}</p>
              </div>
            ))}
          </div>
        </section>
      )}
      <Modal open={open} title="Undang pengguna" onClose={() => setOpen(false)}>
        <UserForm roles={assignable} onDone={() => { setOpen(false); load(); }} />
      </Modal>
    </>
  );
}

function UserForm({ roles, onDone }: { roles: Role[]; onDone: () => void }) {
  const [f, setF] = useState({ email: "", full_name: "", password: "" });
  const [picked, setPicked] = useState<string[]>(["VIEWER"]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (picked.length === 0) { setErr("Pilih minimal satu peran"); return; }
    setBusy(true);
    try { await api.post("/users", { ...f, roles: picked }); onDone(); }
    catch (x) { setErr(errorText(x)); } finally { setBusy(false); }
  }
  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      {err && <Alert>{err}</Alert>}
      <Field label="Nama lengkap">{(id) => <Input id={id} value={f.full_name} onChange={set("full_name")} required />}</Field>
      <Field label="Email">{(id) => <Input id={id} type="email" value={f.email} onChange={set("email")} required />}</Field>
      <Field label="Password awal" hint="Minimal 12 karakter, gabungan 3 dari: huruf kecil, huruf besar, angka, simbol">
        {(id) => <Input id={id} type="password" autoComplete="new-password" value={f.password} onChange={set("password")} required />}
      </Field>
      <fieldset>
        <legend className="mb-2 text-sm font-semibold">Peran</legend>
        <div className="grid gap-2 sm:grid-cols-2">
          {roles.map((r) => (
            <label key={r.code} className="flex min-h-11 items-center gap-2 rounded-lg border border-concrete-line px-3 text-sm">
              <input type="checkbox" className="h-4 w-4 accent-ink" checked={picked.includes(r.code)}
                     onChange={(e) => setPicked(e.target.checked ? [...picked, r.code] : picked.filter((x) => x !== r.code))} />
              {r.name}
            </label>
          ))}
        </div>
      </fieldset>
      <Button type="submit" loading={busy}>Buat pengguna</Button>
    </form>
  );
}
