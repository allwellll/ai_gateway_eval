export function protocolForModel(model) {
  return /claude|opus|sonnet|haiku/i.test(model) ? 'messages' : 'responses';
}
export function endpointFor(input, model) {
  const raw = input.trim();
  if (!raw) throw new Error('请输入接口地址');
  const url = new URL(raw.includes('://') ? raw : `https://${raw}`);
  if (!['http:', 'https:'].includes(url.protocol) || !url.hostname) throw new Error('请输入有效的 HTTP(S) 地址');
  if (url.username || url.password || url.search || url.hash) throw new Error('地址不能包含用户名、密码、查询参数或 fragment');
  let path = url.pathname.replace(/\/+$/, '');
  if (/\/(responses|messages|chat\/completions)$/.test(path)) path = path.replace(/\/(responses|messages|chat\/completions)$/, '');
  else if (!/\/v\d+(?:beta\d*)?$/.test(path)) path += '/v1';
  url.pathname = `${path}/${protocolForModel(model)}`;
  return url.href;
}
