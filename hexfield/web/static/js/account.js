// Accounts on a public server (hexfield-web --accounts): passkey sign-up and sign-in,
// and the admin's list of who may host. The server checks everything; this asks and shows.

import { openNewGame } from "./dialogs.js";
import { modal, toast } from "./effects.js";
import { G, S, api } from "./state.js";
import { $, esc } from "./util.js";

const b64u = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
const bytes = (s) => Uint8Array.from(atob(s.replaceAll("-", "+").replaceAll("_", "/")), (c) => c.charCodeAt(0));

// who this browser is: { accounts, admin, host, lan, user: { name, host } | null }. If the
// server can't be asked, the first time assumes a plain local server; later it throws.
export async function loadMe() {
  try { G.me = await api("me"); } catch (e) {
    if (G.me) throw e;
    G.me = { accounts: false, admin: false, host: true, user: null, lan: null };
  }
  const me = G.me;
  const btn = $("#btn-account");
  btn.hidden = !me.accounts;
  btn.querySelector("span").textContent = me.user ? me.user.name : me.admin ? "Admin" : "Sign in";
  // the new game dialog can't be closed before there is a game: the account is reachable from it too
  $("#new-account").hidden = !me.accounts || !(me.user || me.admin);
  $("#new-account").textContent = me.admin ? "Accounts…" : "Account…";
  $("#new-account").title = me.user ? `Signed in as ${me.user.name}` : "Approve who may host";
  // what only a host can use (the server refuses the rest anyway)
  for (const id of ["#btn-new", "#btn-games", "#over-replay"]) $(id).hidden = !me.host;
  return me;
}

async function passkey(fn) {
  if (!window.PublicKeyCredential || !isSecureContext) {
    return toast("Passkeys need a secure page", "Open this site over HTTPS.", "var(--worst)");
  }
  try {
    await fn();
    location.reload();
  } catch (e) {
    if (e.name === "NotAllowedError" || e.name === "AbortError") toast("No passkey used", "", "var(--muted)", 2200);
    else toast("Couldn't sign in", e.message, "var(--worst)");
  }
}

const signIn = () => passkey(async () => {
  const o = await api("signin-options", {});
  const c = await navigator.credentials.get({ publicKey: { ...o, challenge: bytes(o.challenge) } });
  await api("signin", { id: b64u(c.rawId), clientData: b64u(c.response.clientDataJSON),
    authData: b64u(c.response.authenticatorData), signature: b64u(c.response.signature) });
});

const signUp = (name) => passkey(async () => {
  const o = await api("signup-options", { name });
  const c = await navigator.credentials.create({ publicKey: { ...o, challenge: bytes(o.challenge), user: { ...o.user, id: bytes(o.user.id) } } });
  const r = c.response;
  await api("signup", { clientData: b64u(r.clientDataJSON), authData: b64u(r.getAuthenticatorData()),
    publicKey: b64u(r.getPublicKey()), alg: r.getPublicKeyAlgorithm() });
});

// approved yet? then on to the new game dialog, without a reload
async function checkAgain() {
  let me;
  try { me = await loadMe(); } catch (e) { return toast("Couldn't check", e.message, "var(--worst)"); }
  if (!me.host) return toast("Not yet", `The admin hasn't let ${me.user?.name ?? "you"} host yet.`, "var(--muted)", 2600);
  $("#dlg-account").close();
  if (!S) openNewGame(false); // the new game dialog is the news: no toast on top of it
}

async function signOut() {
  try { await api("signout", {}); } catch { /* the cookie goes either way on reload */ }
  location.reload();
}

// The account dialog: sign in or up, a note for someone who can't host yet, or (from the
// top bar) who you are, with the admin's list of accounts.
export function openAccount() {
  const me = G.me, dlg = $("#dlg-account"), body = $("#account-body");
  const invited = new URLSearchParams(location.search).has("g");
  // closable once there is something behind it: a game, or the means to start one
  const closable = me.host || !!S;
  dlg.setAttribute("closedby", closable ? "any" : "none");
  $("#account-close").hidden = !closable;
  if (!me.user && !me.admin) {
    $("#account-title").textContent = "Sign in to play";
    body.innerHTML = `<p class="muted">${invited ? "You've been invited to a game: sign in to take your seat." : "Accounts here use passkeys: no password, no email."}</p>
      <div class="dlg-actions"><button class="btn primary obvious" id="acct-signin" autofocus>Sign in with a passkey</button></div>
      <form id="acct-signup" class="acct-new"><h3>New here?</h3>
        <label class="field">Your name<input name="name" maxlength="14" autocomplete="nickname" required></label>
        <div class="dlg-actions"><button class="btn">Create an account</button></div></form>`;
  } else if (!me.host) {
    $("#account-title").textContent = `Hi, ${me.user.name}`;
    body.innerHTML = `<p>You can play in any game you're invited to: open its invite link.</p>
      <p class="muted">Starting your own games needs the admin's approval. Ask them to let <b>${esc(me.user.name)}</b> host.</p>
      <div class="dlg-actions"><button class="btn ghost" id="acct-signout">Sign out</button><button class="btn primary obvious" id="acct-check" autofocus>Check again</button></div>`;
  } else {
    $("#account-title").textContent = me.admin ? "Accounts" : `Signed in as ${me.user.name}`;
    body.innerHTML = `${me.admin ? `<p class="muted">Anyone can make an account and join the games they're invited to. Tick the ones that may host games of their own.</p><div id="acct-users" class="table-x"><p class="muted">Loading…</p></div>`
      : `<p>You can host games. They're saved to your account, and only you see them under Games.</p>`}
      ${me.user ? `<div class="dlg-actions">${me.admin ? `<span class="muted acct-as">Signed in as ${esc(me.user.name)}</span>` : ""}<button class="btn ghost" id="acct-signout">Sign out</button></div>` : ""}`;
    if (me.admin) loadUsers();
  }
  modal(dlg);
}

async function loadUsers(data) {
  try {
    const { users } = data || await api("accounts");
    $("#acct-users").innerHTML = users.length ? `<table class="info-table"><thead><tr><th>Name</th><th>Joined</th><th>Last sign-in</th><th>May host</th><th></th></tr></thead><tbody>${
      users.map((u) => `<tr><td>${esc(u.name)}</td><td>${new Date(u.created * 1000).toLocaleDateString()}</td><td>${u.seen ? new Date(u.seen * 1000).toLocaleDateString() : "—"}</td>
        <td><input type="checkbox" data-user="${esc(u.id)}" ${u.host ? "checked" : ""} aria-label="${esc(u.name)} may host"></td>
        <td><button class="btn ghost" data-delete="${esc(u.id)}" data-name="${esc(u.name)}">Delete</button></td></tr>`).join("")}</tbody></table>`
      : `<p class="muted">No accounts yet.</p>`;
  } catch (e) {
    $("#acct-users").innerHTML = `<p class="muted">Couldn't load the accounts: ${esc(e.message)}</p>`;
  }
}

export function wireAccount() {
  $("#btn-account").onclick = () => openAccount();
  $("#new-account").onclick = () => openAccount();
  const body = $("#account-body");
  body.addEventListener("click", (e) => {
    if (e.target.id === "acct-signin") signIn();
    else if (e.target.id === "acct-signout") signOut();
    else if (e.target.id === "acct-check") checkAgain();
    else if (e.target.dataset.delete && confirm(`Delete ${e.target.dataset.name}'s account and saved games?`)) {
      api("accounts", { id: e.target.dataset.delete, delete: true }).then(loadUsers, (err) => toast("Couldn't delete", err.message, "var(--worst)"));
    }
  });
  body.addEventListener("submit", (e) => {
    e.preventDefault();
    signUp(e.target.elements.name.value);
  });
  body.addEventListener("change", async (e) => {
    const id = e.target.dataset.user;
    if (!id) return;
    try { await loadUsers(await api("accounts", { id, host: e.target.checked })); } catch (err) {
      toast("Couldn't change that", err.message, "var(--worst)");
      e.target.checked = !e.target.checked;
    }
  });
}
