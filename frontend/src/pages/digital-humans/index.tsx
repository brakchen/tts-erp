import { useEffect, useState } from 'react';
import { Alert, Button, Input, Label, TextArea, TextField } from '@heroui/react';
import type { PageProps } from '../../shared/contracts';
import { ApiError, requestJson } from '../../shared/api/client';
import { parseIdentityJson } from '../../shared/api/identity-json';
import {
  decodeAssetList,
  decodeDigitalHumanDetail,
  decodeDigitalHumanList,
  displayBytes,
  type Asset,
  type DigitalHuman,
} from '../media-assets/contracts';

const api = '/v2/media';

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
  const [humans, setHumans] = useState<DigitalHuman[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<DigitalHuman | null>(null);
  const [detailAssets, setDetailAssets] = useState<Asset[]>([]);
  const [detailError, setDetailError] = useState('');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [notice, setNotice] = useState('');
  const [attachId, setAttachId] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setLoading(true);
    setListError('');
    void requestJson<unknown>(root, `${api}/digital-humans`, { signal: controller.signal }, parseIdentityJson)
      .then((result) => {
        if (!active) return;
        setHumans(decodeDigitalHumanList(result.data));
      })
      .catch((error: unknown) => {
        if (!active) return;
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
  }, [root, revision]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      setDetailAssets([]);
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
        setDetailAssets(row.assets);
        setName(row.name);
        setDescription(row.description ?? '');
      })
      .catch((error: unknown) => {
        if (!active) return;
        setDetail(null);
        setDetailAssets([]);
        setDetailError(readFailure(error));
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [root, selectedId, revision]);

  async function createHuman() {
    if (!canWrite || !name.trim()) return;
    setNotice('');
    try {
      const created = await requestJson<unknown>(
        root,
        `${api}/digital-humans`,
        {
          method: 'POST',
          body: JSON.stringify({ name: name.trim(), description: description.trim() || null }),
        },
        parseIdentityJson,
      ).then((result) => decodeDigitalHumanDetail(result.data));
      setNotice(`已创建数字人：${created.name}`);
      setName('');
      setDescription('');
      setRevision((value) => value + 1);
    } catch (error: unknown) {
      setNotice(`创建未完成：${writeFailure(error)}`);
    }
  }

  async function saveHuman() {
    if (!detail || !canWrite) return;
    setNotice('');
    try {
      await requestJson<unknown>(
        root,
        `${api}/digital-humans/${detail.digitalHumanId}`,
        {
          method: 'PATCH',
          body: JSON.stringify({
            name: name.trim(),
            description: description.trim() || null,
          }),
        },
        parseIdentityJson,
      );
      setNotice('数字人信息已保存。');
      setRevision((value) => value + 1);
    } catch (error: unknown) {
      setNotice(`保存未完成：${writeFailure(error)}`);
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
      setSelectedId(null);
      setDetail(null);
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
            `${api}/assets?digitalHumanId=${detail.digitalHumanId}&limit=200`,
            {},
            parseIdentityJson,
          )
        ).data,
      );
      const attached = new Set(detail.assetIds);
      const candidates = page.items.filter((asset) => !attached.has(asset.assetId));
      if (candidates.length === 0) {
        setNotice('资产库中没有未挂载的资产。');
        return;
      }
      setAttachId(candidates[0].assetId);
      setNotice(
        `候选资产：${candidates
          .slice(0, 5)
          .map((asset) => `${asset.assetId} ${asset.filename}`)
          .join('；')}`,
      );
    } catch (error: unknown) {
      setNotice(`候选读取失败：${readFailure(error)}`);
    }
  }

  return (
    <div className="erp-page">
      <div className="erp-panel space-y-3">
        <h2 className="text-xl font-semibold">新建数字人</h2>
        <p>数字人是人设档案容器，用来组织它引用的图片与视频资产。</p>
        <TextField value={name} isDisabled={!canWrite} onChange={setName}>
          <Label>名称</Label>
          <Input />
        </TextField>
        <TextField value={description} isDisabled={!canWrite} onChange={setDescription}>
          <Label>人设说明</Label>
          <TextArea rows={3} autoComplete="off" />
        </TextField>
        <Button isDisabled={!canWrite || !name.trim()} onPress={() => void createHuman()}>
          创建数字人
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
            </Alert.Content>
          </Alert>
        )}
        <ul className="space-y-2">
          {humans.map((human) => (
            <li key={human.digitalHumanId} className="flex flex-wrap items-center gap-3">
              <Button variant="secondary" onPress={() => setSelectedId(human.digitalHumanId)}>
                查看 {human.name}
              </Button>
              <span>
                {human.status === 'archived' ? '已归档' : '启用中'} ·{' '}
                {human.assetIds.length} 个资产
              </span>
            </li>
          ))}
        </ul>
        {!loading && !listError && humans.length === 0 && <p>当前还没有数字人。</p>}
      </div>

      {selectedId && (
        <div className="erp-panel space-y-3">
          <h2 className="text-xl font-semibold">数字人详情</h2>
          {detailError && (
            <Alert status="danger">
              <Alert.Content>
                <Alert.Title>详情不可用</Alert.Title>
                <Alert.Description>{detailError}</Alert.Description>
              </Alert.Content>
            </Alert>
          )}
          {detail && (
            <>
              <p>
                {detail.name} · {detail.status === 'archived' ? '已归档' : '启用中'}
              </p>
              <TextField value={name} isDisabled={!canWrite} onChange={setName}>
                <Label>名称</Label>
                <Input />
              </TextField>
              <TextField value={description} isDisabled={!canWrite} onChange={setDescription}>
                <Label>人设说明</Label>
                <TextArea rows={3} autoComplete="off" />
              </TextField>
              <div className="flex flex-wrap gap-2">
                <Button isDisabled={!canWrite || !name.trim()} onPress={() => void saveHuman()}>
                  保存
                </Button>
                <Button variant="secondary" isDisabled={!canWrite} onPress={() => void archiveHuman()}>
                  归档
                </Button>
                <Button variant="danger" isDisabled={!canWrite} onPress={() => void removeHuman()}>
                  删除
                </Button>
              </div>

              <h3 className="text-lg font-semibold">引用资产</h3>
              <ul className="space-y-2">
                {detailAssets.map((asset) => (
                  <li key={asset.assetId} className="flex flex-wrap items-center gap-3">
                    <span>
                      {asset.filename} ·{' '}
                      {asset.kind === 'video' ? '视频' : '图片'} ·{' '}
                      {displayBytes(asset.sizeBytes)}
                    </span>
                    <Button
                      variant="secondary"
                      isDisabled={!canWrite}
                      onPress={() => void detachAsset(asset.assetId)}
                    >
                      解绑
                    </Button>
                  </li>
                ))}
              </ul>
              {!detailAssets.length && <p>当前数字人还没有引用资产。</p>}

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
                <Button isDisabled={!canWrite || !attachId.trim()} onPress={() => void attachAsset()}>
                  挂载
                </Button>
                <Button variant="secondary" isDisabled={!canWrite} onPress={() => void listUnattachedAssets()}>
                  列出可挂载资产
                </Button>
              </div>
            </>
          )}
        </div>
      )}

      {notice && <p role="status">{notice}</p>}
    </div>
  );
}
