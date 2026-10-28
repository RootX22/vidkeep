// The popup asks the vidkeep program on this computer, through Firefox's native messaging, to look
// up the page and to download it. Downloads run in vidkeep, so they go on after the popup closes.

const HOST = 'io.github.rootx22.vidkeep';
const POLL_MS = 1000;
const SIGN_IN_BROWSERS = ['firefox', 'chrome', 'brave', 'edge', 'chromium', 'safari', 'opera', 'vivaldi'];
const PLAYLIST_QUALITIES = ['best', '2160', '1440', '1080', '720', '480', '360'];
const STAGES = {
  Merger: 'Joining video and audio',
  FFmpegMetadata: 'Adding title and chapters',
  EmbedThumbnail: 'Adding cover image',
  FFmpegEmbedSubtitle: 'Adding subtitles',
  FFmpegSubtitlesConvertor: 'Converting subtitles',
  FFmpegExtractAudio: 'Converting audio',
};

/** vidkeep answered but said no; anything else thrown by sendNativeMessage means it is not installed. */
class Refused extends Error {}

const $ = (id) => document.getElementById(id);
const settings = await browser.storage.local.get({ folder: '', browser: '', subs: false, subsLangs: 'en' });
let pageUrl = null;
let info = null;

async function ask(message) {
  const reply = await browser.runtime.sendNativeMessage(HOST, message);
  if (!reply.ok) throw new Refused(reply.error);
  return reply;
}

function h(tag, attributes = {}, ...children) {
  const element = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (value === false || value === null || value === undefined) continue;
    if (name.startsWith('on')) element.addEventListener(name.slice(2), value);
    else element.setAttribute(name, value === true ? '' : value);
  }
  element.append(...children.filter((child) => child !== null && child !== false));
  return element;
}

/** Sizes the way the vidkeep command prints them. */
function size(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(0)} KiB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(2)} MiB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GiB`;
}

function clock(seconds) {
  const whole = Math.round(seconds);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const secs = String(whole % 60).padStart(2, '0');
  return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${secs}` : `${minutes}:${secs}`;
}

async function targetUrl() {
  const given = new URLSearchParams(location.search).get('url');
  if (given) return given;
  const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
  return tab?.url ?? null;
}

function showStatus(text) {
  $('status').hidden = !text;
  $('status').textContent = text ?? '';
}

function showProblem(message) {
  $('problem').hidden = !message;
  $('problem-text').textContent = message ?? '';
  $('sign-in').hidden = !message || !/confirm you|sign in|cookies/i.test(message);
}

async function lookUp() {
  showProblem(null);
  $('video').hidden = true;
  showStatus('Looking up this page…');
  try {
    info = (await ask({ type: 'info', url: pageUrl, browser: settings.browser })).info;
    showVideo();
  } catch (error) {
    if (!(error instanceof Refused)) throw error;
    showProblem(error.message);
  } finally {
    showStatus(null);
  }
}

function showVideo() {
  const playlist = info.playlist_count > 1;
  $('thumbnail').hidden = !info.thumbnail;
  if (info.thumbnail) $('thumbnail').src = info.thumbnail;
  $('title').textContent = info.title ?? pageUrl;
  const details = playlist
    ? [`Playlist of ${info.playlist_count} videos`]
    : [info.channel, info.duration_string, info.chapters ? `${info.chapters} chapters` : null];
  $('meta').textContent = details.filter(Boolean).join(' · ');
  $('choices').hidden = playlist;
  $('playlist').hidden = !playlist;
  if (!playlist) $('choices').replaceChildren(...choiceRows());
  $('download').textContent = playlist ? `Download ${info.playlist_count} videos` : 'Download';
  $('video').hidden = false;
}

function choiceRows() {
  const rows = info.options.map((option) => ({
    format: option.format,
    label: `${option.height}p`,
    codec: option.codec,
    size: size(option.size) + (option.exact ? '' : ' ~'),
    plays: `QuickTime: ${option.quicktime}`,
  }));
  if (info.audio_only) {
    rows.push({ format: info.audio_only.format, label: 'Audio', codec: 'm4a', size: size(info.audio_only.size), plays: '' });
  }
  // Plain files (a direct link, many small sites) list no resolutions; save them as they are.
  if (!rows.length && info.default_format) {
    rows.push({ format: info.default_format, label: 'Original', codec: '', size: 'size unknown', plays: '' });
  }
  const chosen = info.default_format ?? rows[0]?.format;
  return rows.map((row) =>
    h(
      'label',
      { class: 'choice' },
      h('input', { type: 'radio', name: 'format', value: row.format, checked: row.format === chosen }),
      h('span', {}, row.label),
      h('span', {}, row.codec),
      h('span', { class: 'size' }, row.size),
      h('span', { class: 'plays' }, row.plays),
    ),
  );
}

async function download() {
  const request = { type: 'get', url: pageUrl, folder: settings.folder, browser: settings.browser };
  const format = document.querySelector('input[name="format"]:checked')?.value;
  if (info.playlist_count > 1) {
    request.quality = $('playlist-quality').value;
    request.codec = $('playlist-codec').value;
  } else {
    request.format = format;
  }
  const audioOnly = format !== undefined && format === info.audio_only?.format;
  if ($('subs').checked && !audioOnly) request.subs = $('subs-langs').value.replace(/\s+/g, '');
  $('download').disabled = true;
  try {
    await ask(request);
    showProblem(null);
    await refreshJobs();
  } catch (error) {
    if (!(error instanceof Refused)) throw error;
    showProblem(error.message);
  } finally {
    $('download').disabled = false;
  }
}

async function refreshJobs() {
  const { jobs } = await ask({ type: 'jobs' });
  $('downloads-section').hidden = jobs.length === 0;
  $('downloads').replaceChildren(...jobs.map(jobItem));
}

function jobItem(job) {
  const videos = job.videos;
  const finished = videos.filter((video) => video.status === 'done' || video.status === 'skipped').length;
  const total = videos.reduce((sum, video) => sum + (video.size ?? 0), 0);
  const fetched = videos.reduce(
    (sum, video) => sum + (video.status === 'done' ? (video.size ?? 0) : (video.downloaded ?? 0)),
    0,
  );
  const fill = h('span');
  fill.style.width = `${total ? Math.min(100, (fetched / total) * 100) : 0}%`;
  const prefix = videos.length > 1 ? `${finished} of ${videos.length} videos · ` : '';
  return h(
    'li',
    { class: 'job' },
    h('div', { class: 'top' }, h('span', { class: 'name' }, videos.find((video) => video.title)?.title ?? job.url), action(job)),
    job.status === 'running' || job.status === 'stopped' ? h('div', { class: 'bar' }, fill) : null,
    h('div', { class: job.status === 'failed' ? 'detail error' : 'detail' }, prefix + jobDetail(job, fetched, total)),
  );
}

function jobDetail(job, fetched, total) {
  if (job.status === 'done') return `Saved · ${size(total)}`;
  if (job.status === 'failed') return job.error;
  if (job.status === 'stopped') return `Stopped at ${size(fetched)} of ${size(total)}`;
  const stage = job.videos.find((video) => video.status === 'processing')?.stage;
  if (stage) return STAGES[stage] ?? 'Finishing';
  const speed = job.videos.reduce((sum, video) => sum + (video.status === 'downloading' ? (video.speed ?? 0) : 0), 0);
  if (!total) return 'Starting…';
  const left = speed > 0 ? ` · ${clock((total - fetched) / speed)} left` : '';
  return `${size(fetched)} of ${size(total)} · ${size(speed)}/s${left}`;
}

function action(job) {
  const button = (label, message) =>
    h('button', { class: 'link', type: 'button', onclick: () => ask(message).then(refreshJobs, showRefusal) }, label);
  if (job.status === 'running') return button('Stop', { type: 'stop', job: job.id });
  if (job.status === 'done') return button('Show in folder', { type: 'reveal', job: job.id });
  return button(job.status === 'stopped' ? 'Resume' : 'Try again', { type: 'retry', job: job.id });
}

function showRefusal(error) {
  if (!(error instanceof Refused)) throw error;
  showProblem(error.message);
}

function wireSettings() {
  $('folder').value = settings.folder;
  $('folder').addEventListener('change', () => {
    settings.folder = $('folder').value.trim();
    browser.storage.local.set({ folder: settings.folder });
  });
  $('subs').checked = settings.subs;
  $('subs-langs').value = settings.subsLangs;
  $('subs').addEventListener('change', () => browser.storage.local.set({ subs: $('subs').checked }));
  $('subs-langs').addEventListener('change', () => browser.storage.local.set({ subsLangs: $('subs-langs').value }));
  $('sign-in-browser').replaceChildren(...SIGN_IN_BROWSERS.map((name) => h('option', { value: name }, name)));
  $('sign-in-browser').value = settings.browser || 'firefox';
  $('sign-in-retry').addEventListener('click', () => {
    settings.browser = $('sign-in-browser').value;
    browser.storage.local.set({ browser: settings.browser });
    lookUp();
  });
  $('playlist-quality').replaceChildren(
    ...PLAYLIST_QUALITIES.map((quality) =>
      h('option', { value: quality, selected: quality === '1080' }, quality === 'best' ? 'best' : `${quality}p`),
    ),
  );
  $('download').addEventListener('click', download);
  $('clear').addEventListener('click', () => ask({ type: 'forget' }).then(refreshJobs, showRefusal));
}

async function main() {
  wireSettings();
  try {
    const hello = await ask({ type: 'hello' });
    $('version').textContent = `vidkeep ${hello.version}`;
    $('folder').placeholder = hello.folder;
  } catch (error) {
    if (error instanceof Refused) throw error;
    $('setup').hidden = false;
    return;
  }
  setInterval(refreshJobs, POLL_MS);
  refreshJobs();
  pageUrl = await targetUrl();
  if (pageUrl && /^https?:/.test(pageUrl)) lookUp();
  else showStatus('Open the page of a video, then click vidkeep.');
}

main();
