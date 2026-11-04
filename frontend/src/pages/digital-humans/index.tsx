import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Label, TextArea, TextField } from '@heroui/react';
import type { PageProps } from '../../shared/contracts';
import { ApiError, requestJson } from '../../shared/api/client';
import { parseIdentityJson } from '../../shared/api/identity-json';
import DeleteConfirmation from '../media-assets/DeleteConfirmation';
import {
  decodeAssetList,
  decodeDigitalHuman,
  decodeDigitalHumanDetail,
  decodeDigitalHumanList,
  displayBytes,
  type Asset,
  type DigitalHuman,
  type DigitalHumanDetail,
} from '../media-assets/contracts';

const api = '/v2/media';
const pageSize = 50;

function readFailure(error: unknown): string {
  if (!(error instanceof ApiError)) return '读取未完成，请手动重试';
  if (error.status === 401) return '会话已失效（HTTP 401），请重新登录';
  if (error.status === 403) return '无权读取数字人（HTTP 403）';
  if (error.status === 404) return '当前记录不存在（HTTP 404）';
  if (error.status === 429) return '请求过于频繁（HTTP 429），请等待后手动重试';
  return `读取失败（HTTP ${error.status}），请手动重试`;
}

function writeFailure(error: unknown): string {
  if (!(error instanceof ApiError)) return '操作未完成，请手动重试';
  if (error.status === 401) return '会话已失效（HTTP 401），请重新登录';
  if (error.status === 403) return '无权执行该操作（HTTP 403）';
  if (error.status === 404) return '目标不存在（HTTP 404）';
  if (error.status === 429) return '请求过于频繁（HTTP 429），请等待后手动重试';
  return `操作失败（HTTP ${error.status}）`;
}

export default function DigitalHumans({ bootstrap }: PageProps) {
  const root = bootstrap.rootPath;
  const canWrite = bootstrap.roleCapabilities?.readwrite === true;
  const [revision, setRevision] = useState(0);
  const [detailRevision, setDetailRevision] = useState(0);
  const [offset, setOffset] = useState(0);
  const [hasNext, setHasNext] = useState(false);
  const [humans, setHumans] = useState<DigitalHuman[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loadedDetail, setDetail] = useState<DigitalHumanDetail | null>(null);
  const detail = loadedDetail?.digitalHumanId === selectedId ? loadedDetail : null;
  const [detailError, setDetailError] = useState('');
  const [newName, setNewName] = useState('');
  const [newDescription, setNewDescription] = useState('');
  const [creating, setCreating] = useState(false);
  const createInFlight = useRef(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [notice, setNotice] = useState('');
  const [saving, setSaving] = useState(false);
  const saveInFlight = useRef(false);
  const [attachId, setAttachId] = useState('');
  const [candidateResult, setCandidateResult] = useState<{ humanId: string; assets: Asset[] }>();
  const candidates = candidateResult?.humanId === selectedId ? candidateResult.assets : undefined;

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setLoading(true);
    setListError('');
    void requestJson<unknown>(root, `${api}/digital-humans?limit=${pageSize + 1}&offset=${offset}`, { signal: controller.signal }, parseIdentityJson)
      .then((result) => {
        if (!active) return;
        const rows = decodeDigitalHumanList(result.data);
        setHasNext(rows.length > pageSize);
        setHumans(rows.slice(0, pageSize));
      })
      .catch((error: unknown) => {
        if (!active) return;
        setHasNext(false);
        setHumans([]);
        setListError(readFailure(error));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [root, revision, offset]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      setDetailError('');
      return;
    }
    const controller = new AbortController();
    let active = true;
    setDetailError('');
    void requestJson<unknown>(root, `${api}/digital-humans/${selectedId}`, { signal: controller.signal }, parseIdentityJson)
      .then((result) => {
        if (!active) return;
        const row = decodeDigitalHumanDetail(result.data);
        setDetail(row);
        setName(row.name);
        setDescription(row.description ?? '');
      })
      .catch((error: unknown) => {
        if (!active) return;
        setDetail(null);
        setDetailError(readFailure(error));
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [root, selectedId, detailRevision]);

  async function createHuman() {
    if (!canWrite || !newName.trim() || createInFlight.current) return;
    createInFlight.current = true;
    setCreating(true);
    setNotice('');
    try {
      const created = await requestJson<unknown>(
        root,
        `${api}/digital-humans`,
        {
          method: 'POST',
          body: JSON.stringify({ name: newName.trim(), description: newDescription.trim() || null }),
        },
        parseIdentityJson,
      ).then((result) => decodeDigitalHuman(result.data));
      setNotice(`已创建数字人：${created.name}`);
      setNewName('');
      setNewDescription('');
      setOffset(0);
      setRevision((value) => value + 1);
    } catch (error: unknown) {
      setNotice(`创建未完成：${writeFailure(error)}`);
    } finally {
      createInFlight.current = false;
      setCreating(false);
    }
  }

  async function saveHuman() {
    if (!detail || !canWrite || !name.trim() || saveInFlight.current) return;
    saveInFlight.current = true;
    setSaving(true);
    setNotice('');
    try {
      const updated = await requestJson<unknown>(
        root,
        `${api}/digital-humans/${detail.digitalHumanId}`,
        {
          method: 'PATCH',
          body: JSON.stringify({
            name: name.trim(),
            description: description.trim(),
          }),
        },
        parseIdentityJson,
      ).then((result) => decodeDigitalHuman(result.data));
      setDetail((current) => current?.digitalHumanId === updated.digitalHumanId
        ? { ...current, ...updated }
        : current);
      setNotice('数字人信息已保存。');
      setRevision((value) => value + 1);
    } catch (error: unknown) {
      setNotice(`保存未完成：${writeFailure(error)}`);
    } finally {
      saveInFlight.current = false;
      setSaving(false);
    }
  }

  async function archiveHuman() {
    if (!detail || !canWrite) return;
    setNotice('');
    try {
      await requestJson<unknown>(
        root,
        `${api}/digital-humans/${detail.digitalHumanId}`,
        { method: 'PATCH', body: JSON.stringify({ status: 'archived' }) },
        parseIdentityJson,
      );
      setNotice('数字人已归档。');
      setDetailRevision((value) => value + 1);
      setRevision((value) => value + 1);
    } catch (error: unknown) {
      setNotice(`归档未完成：${writeFailure(error)}`);
    }
  }

  async function removeHuman() {
    if (!detail || !canWrite) return;
    setNotice('');
    try {
      await requestJson<unknown>(root, `${api}/digital-humans/${detail.digitalHumanId}`, {
        method: 'DELETE',
      });
      setNotice('数字人已删除；其资产仍保留在资产库中。');
      setSelectedId((current) => current === detail.digitalHumanId ? null : current);
      setDetail((current) => current?.digitalHumanId === detail.digitalHumanId ? null : current);
      if (humans.length === 1 && humans[0].digitalHumanId === detail.digitalHumanId) {
        setOffset(Math.max(0, offset - pageSize));
      }
      setRevision((value) => value + 1);
    } catch (error: unknown) {
      setNotice(`删除未完成：${writeFailure(error)}`);
    }
  }

  async function attachAsset() {
    if (!detail || !canWrite || !attachId.trim()) return;
    setNotice('');
    try {
      await requestJson<unknown>(
        root,
        `${api}/digital-humans/${detail.digitalHumanId}/assets`,
        { method: 'POST', body: JSON.stringify({ assetId: attachId.trim() }) },
        parseIdentityJson,
      );
      setNotice('资产已挂载。');
      setAttachId('');
      setCandidateResult(undefined);
      setDetailRevision((value) => value + 1);
      setRevision((value) => value + 1);
    } catch (error: unknown) {
      setNotice(`挂载未完成：${writeFailure(error)}`);
    }
  }

  async function detachAsset(assetId: string) {
    if (!detail || !canWrite) return;
    setNotice('');
    try {
      await requestJson<unknown>(
        root,
        `${api}/digital-humans/${detail.digitalHumanId}/assets/${assetId}`,
        { method: 'DELETE' },
      );
      setNotice('资产已解绑。');
      setCandidateResult(undefined);
      setDetailRevision((value) => value + 1);
      setRevision((value) => value + 1);
    } catch (error: unknown) {
      setNotice(`解绑未完成：${writeFailure(error)}`);
    }
  }

  async function listUnattachedAssets() {
    if (!detail || !canWrite) return;
    setNotice('');
    try {
      const page = decodeAssetList(
        (
          await requestJson<unknown>(
            root,
            `${api}/assets?excludeDigitalHumanId=${detail.digitalHumanId}&limit=200`,
            {},
            parseIdentityJson,
          )
        ).data,
      );
      setCandidateResult({ humanId: detail.digitalHumanId, assets: page.items });
    } catch (error: unknown) {
      setNotice(`候选读取失败：${readFailure(error)}`);
    }
  }

  return (
    <div className="erp-page">
      <div className="erp-panel space-y-3">
        <h2 className="text-xl font-semibold">新建数字人</h2>
        <p>数字人是人设档案容器，用来组织它引用的图片与视频资产。</p>
        <TextField value={newName} isDisabled={!canWrite || creating} onChange={setNewName}>
          <Label>名称</Label>
          <Input maxLength={120} />
        </TextField>
        <TextField value={newDescription} isDisabled={!canWrite || creating} onChange={setNewDescription}>
          <Label>人设说明</Label>
          <TextArea rows={3} autoComplete="off" maxLength={2000} />
        </TextField>
        <Button isDisabled={!canWrite || !newName.trim()} isPending={creating} onPress={() => void createHuman()}>
          {creating ? '创建中…' : '创建数字人'}
        </Button>
      </div>

      <div className="erp-panel space-y-3">
        <h2 className="text-xl font-semibold">数字人列表</h2>
        {loading && <p role="status">加载数字人中…</p>}
        {listError && (
          <Alert status="danger">
            <Alert.Content>
              <Alert.Title>数字人列表不可用</Alert.Title>
              <Alert.Description>{listError}</Alert.Description>
              <Button variant="secondary" onPress={() => setRevision((value) => value + 1)}>重试列表</Button>
            </Alert.Content>
          </Alert>
        )}
        <ul className="space-y-2">
          {humans.map((human) => (
            <li key={human.digitalHumanId} className="flex flex-wrap items-center gap-3">
              <Button variant="secondary" onPress={() => {
                setAttachId('');
                setCandidateResult(undefined);
                setSelectedId(human.digitalHumanId);
              }}>
                查看 {human.name}
              </Button>
              <span>
                {human.status === 'archived' ? '已归档' : '启用中'} ·{' '}
                {human.assetIds.length} 个资产
              </span>
            </li>
          ))}
        </ul>
        {(offset > 0 || hasNext) && (
          <nav aria-label="数字人分页" className="flex flex-wrap gap-2">
            <Button variant="secondary" isDisabled={loading || offset === 0} onPress={() => setOffset(offset - pageSize)}>上一页</Button>
            <Button variant="secondary" isDisabled={loading || !hasNext} onPress={() => setOffset(offset + pageSize)}>下一页</Button>
          </nav>
        )}
        {!loading && !listError && humans.length === 0 && <p>当前还没有数字人。</p>}
      </div>

      {selectedId && (
        <div className="erp-panel space-y-3">
          <h2 className="text-xl font-semibold">数字人详情</h2>
          {!detail && !detailError && <p role="status">加载数字人详情中…</p>}
          {detailError && (
            <Alert status="danger">
              <Alert.Content>
                <Alert.Title>详情不可用</Alert.Title>
                <Alert.Description>{detailError}</Alert.Description>
                <Button variant="secondary" onPress={() => setDetailRevision((value) => value + 1)}>重试详情</Button>
              </Alert.Content>
            </Alert>
          )}
          {detail && (
            <>
              <p>
                {detail.name} · {detail.status === 'archived' ? '已归档' : '启用中'}
              </p>
              <TextField value={name} isDisabled={!canWrite || saving} onChange={setName}>
                <Label>名称</Label>
                <Input maxLength={120} />
              </TextField>
              <TextField value={description} isDisabled={!canWrite || saving} onChange={setDescription}>
                <Label>人设说明</Label>
                <TextArea rows={3} autoComplete="off" maxLength={2000} />
              </TextField>
              <div className="flex flex-wrap gap-2">
                <Button isDisabled={!canWrite || !name.trim()} isPending={saving} onPress={() => void saveHuman()}>
                  {saving ? '保存中…' : '保存'}
                </Button>
                <Button variant="secondary" isDisabled={!canWrite || saving} onPress={() => void archiveHuman()}>
                  归档
                </Button>
                <DeleteConfirmation
                  key={detail.digitalHumanId}
                  label="删除数字人"
                  name={detail.name}
                  description="删除后，数字人将从列表移除。它引用的资产仍保留在资产库。"
                  isDisabled={!canWrite || saving}
                  onConfirm={removeHuman}
                />
              </div>

              <h3 className="text-lg font-semibold">引用资产</h3>
              <ul className="space-y-2">
                {detail.assets.map((asset) => (
                  <li key={asset.assetId} className="flex flex-wrap items-center gap-3">
                    <span>
                      {asset.filename} ·{' '}
                      {asset.kind === 'video' ? '视频' : '图片'} ·{' '}
                      {displayBytes(asset.sizeBytes)}
                    </span>
                    <Button
                      variant="secondary"
                      isDisabled={!canWrite || saving}
                      onPress={() => void detachAsset(asset.assetId)}
                    >
                      解绑
                    </Button>
                  </li>
                ))}
              </ul>
              {!detail.assets.length && <p>当前数字人还没有引用资产。</p>}

              <div className="erp-filter-row">
                <label className="erp-field">
                  挂载资产 ID
                  <input
                    className="erp-select"
                    aria-label="挂载资产 ID"
                    value={attachId}
                    disabled={!canWrite}
                    onChange={(event) => setAttachId(event.target.value)}
                  />
                </label>
                <Button isDisabled={!canWrite || saving || !attachId.trim()} onPress={() => void attachAsset()}>
                  挂载
                </Button>
                <Button variant="secondary" isDisabled={!canWrite} onPress={() => void listUnattachedAssets()}>
                  列出可挂载资产
                </Button>
              </div>
              {candidates && (candidates.length > 0 ? (
                <label className="erp-field">
                  可挂载资产
                  <select
                    className="erp-select"
                    value={candidates.some((asset) => asset.assetId === attachId) ? attachId : ''}
                    onChange={(event) => setAttachId(event.target.value)}
                  >
                    <option value="">请选择资产</option>
                    {candidates.map((asset) => (
                      <option key={asset.assetId} value={asset.assetId}>
                        {asset.filename}（#{asset.assetId}）
                      </option>
                    ))}
                  </select>
                </label>
              ) : <p role="status">资产库中没有未挂载的资产。</p>)}
            </>
          )}
        </div>
      )}

      {notice && <p role="status">{notice}</p>}
    </div>
  );
}
