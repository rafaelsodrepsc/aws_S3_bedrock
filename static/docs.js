const Demo = (() => {
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function status(node, text, state = 'info') {
    node.textContent = text;
    node.dataset.state = state;
  }

  function headers(token, json = false) {
    return json ? { 'x-admin-token': token, 'Content-Type': 'application/json' }
      : { 'x-admin-token': token };
  }

  async function readError(res) {
    if (res.status === 401) return 'token inválido';
    if (res.status === 429) return 'limite do Bedrock, tente de novo em alguns segundos';
    try {
      const body = await res.json();
      if (typeof body.detail === 'string') {
        if (body.detail.includes('ConflictException')) return 'já existe uma sincronização em andamento';
        return body.detail;
      }
      return JSON.stringify(body.detail || body);
    } catch {
      return `Erro (${res.status})`;
    }
  }

  function name(item) {
    return item.name || `Documento sem nome (${item.key.split('/').pop().slice(0, 8)})`;
  }

  function size(bytes) {
    const value = Number(bytes || 0);
    return value >= 1024 * 1024 ? `${(value / (1024 * 1024)).toFixed(1)} MB`
      : `${(value / 1024).toFixed(1)} KB`;
  }

  function metadata(item) {
    const date = new Date(item.last_modified);
    const shortDate = Number.isNaN(date.getTime()) ? 'Data indisponível'
      : date.toLocaleDateString('pt-BR');
    return `${size(item.size)} · ${shortDate}`;
  }

  function docs(getToken, onSync = () => {}) {
    const panel = document.getElementById('documents');
    panel.className = 'docs-panel';
    panel.open = !window.matchMedia('(max-width: 800px)').matches;
    const summary = element('summary', 'Documentos na base ');
    const count = element('span', '(0)', 'count');
    summary.append(count);
    const toolbar = element('div', undefined, 'docs-toolbar');
    const refresh = element('button', 'Atualizar lista');
    refresh.type = 'button';
    toolbar.append(refresh);
    const message = element('p', undefined, 'status');
    message.setAttribute('role', 'status');
    const syncMessage = element('p', undefined, 'status');
    syncMessage.setAttribute('role', 'status');
    const list = element('ul', undefined, 'docs-list');
    panel.append(summary, toolbar, message, syncMessage, list);
    let items = [];
    let loadVersion = 0;
    let syncVersion = 0;
    let pollTimer;
    let syncing = false;
    let deleting = false;

    function lockDeletion(locked) {
      deleting = locked;
      refresh.disabled = locked;
      list.querySelectorAll('button').forEach((button) => { button.disabled = locked; });
    }

    function render() {
      list.replaceChildren();
      count.textContent = `(${items.length})`;
      items.forEach((item) => {
        const row = element('li', undefined, 'doc-item');
        const heading = element('div', undefined, 'doc-heading');
        const remove = element('button', '×', 'doc-delete');
        remove.type = 'button';
        remove.setAttribute('aria-label', `Excluir ${name(item)}`);
        heading.append(element('strong', name(item)), remove);
        const confirmation = element('div', undefined, 'confirmation');
        confirmation.hidden = true;
        const actions = element('div', undefined, 'actions');
        const accept = element('button', 'Excluir', 'danger');
        const cancel = element('button', 'Cancelar');
        accept.type = cancel.type = 'button';
        actions.append(accept, cancel);
        confirmation.append(element('p', 'Excluir da base?'), actions);
        remove.onclick = () => {
          confirmation.hidden = false;
          remove.hidden = true;
          cancel.focus();
        };
        cancel.onclick = () => {
          confirmation.hidden = true;
          remove.hidden = false;
          remove.focus();
        };
        accept.onclick = () => deleteDocument(item);
        row.append(heading, element('p', metadata(item), 'doc-meta'), confirmation);
        list.append(row);
      });
      lockDeletion(deleting);
    }

    async function load() {
      const version = ++loadVersion;
      const token = getToken();
      if (!token) {
        items = [];
        render();
        status(message, 'Informe o token para ver os documentos.', 'info');
        return;
      }
      refresh.disabled = true;
      status(message, 'Carregando documentos...', 'loading');
      try {
        const res = await fetch('/admin/docs', { headers: headers(token) });
        if (!res.ok) throw new Error(await readError(res));
        const data = await res.json();
        if (version !== loadVersion || token !== getToken()) return;
        items = data;
        render();
        status(message, items.length ? 'Lista atualizada.' : 'Nenhum documento na base.', items.length ? 'success' : 'info');
      } catch (error) {
        if (version === loadVersion && token === getToken()) status(message, error.message, 'error');
      } finally {
        if (version === loadVersion) refresh.disabled = deleting;
      }
    }

    function syncUpdate(data, text, state) {
      status(syncMessage, text, state);
      onSync(data, text, state);
    }

    function showSync(data) {
      const state = data.status === 'COMPLETE' ? 'success'
        : ['FAILED', 'STOPPED'].includes(data.status) ? 'error' : 'loading';
      syncUpdate(data, `Sincronização: ${data.status}`, state);
      return ['COMPLETE', 'FAILED', 'STOPPED'].includes(data.status);
    }

    async function poll(jobId, token, version) {
      try {
        const res = await fetch(`/admin/sync/${encodeURIComponent(jobId)}`, { headers: headers(token) });
        if (!res.ok) throw new Error(await readError(res));
        const data = await res.json();
        if (version !== syncVersion || token !== getToken()) return;
        if (showSync({ ...data, job_id: jobId })) {
          syncing = false;
          return;
        }
        pollTimer = setTimeout(() => poll(jobId, token, version), 3000);
      } catch (error) {
        if (version !== syncVersion || token !== getToken()) return;
        syncing = false;
        syncUpdate(null, error instanceof TypeError ? 'Falha de rede ao consultar sincronização.' : error.message, 'error');
      }
    }

    async function startSync() {
      const token = getToken();
      const version = syncVersion;
      syncUpdate(null, 'Iniciando sincronização...', 'loading');
      try {
        const res = await fetch('/admin/sync', { method: 'POST', headers: headers(token) });
        if (version !== syncVersion || token !== getToken()) return;
        if (res.status === 409) {
          syncUpdate(null, 'Já há uma sincronização em andamento. Sincronize de novo quando ela terminar.', 'warning');
          return;
        }
        if (!res.ok) throw new Error(await readError(res));
        const data = await res.json();
        if (version !== syncVersion || token !== getToken()) return;
        clearTimeout(pollTimer);
        const pollVersion = ++syncVersion;
        syncing = !showSync(data);
        if (syncing) pollTimer = setTimeout(() => poll(data.job_id, token, pollVersion), 3000);
      } catch (error) {
        if (version !== syncVersion || token !== getToken()) return;
        syncUpdate(null, error instanceof TypeError ? 'Falha de rede ao iniciar sincronização.' : error.message, 'error');
      }
    }

    async function deleteDocument(item) {
      if (deleting) return;
      ++loadVersion;
      const token = getToken();
      lockDeletion(true);
      status(message, 'Excluindo documento...', 'loading');
      try {
        const res = await fetch('/admin/docs/delete', {
          method: 'POST', headers: headers(token, true), body: JSON.stringify({ key: item.key }),
        });
        if (!res.ok) throw new Error(await readError(res));
        if (token !== getToken()) return;
        items = items.filter((doc) => doc.key !== item.key);
        render();
        status(message, 'Documento excluído. Atualizando a base da IA...', 'success');
        await startSync();
        await load();
      } catch (error) {
        if (token === getToken()) status(message, `Falha ao excluir: ${error.message}`, 'error');
      } finally {
        lockDeletion(false);
      }
    }

    function tokenChanged() {
      ++loadVersion;
      ++syncVersion;
      clearTimeout(pollTimer);
      syncing = false;
      items = [];
      render();
      status(syncMessage, '');
      load();
    }

    function sourceName(uri) {
      const key = (uri || '').split('/').slice(-2).join('/');
      const item = items.find((doc) => doc.key === key);
      return item ? name(item) : (uri || '').split('/').pop() || 'Documento sem nome';
    }

    refresh.onclick = load;
    load();
    return { load, startSync, tokenChanged, sourceName };
  }

  return { element, status, headers, readError, name, size, metadata, docs };
})();
