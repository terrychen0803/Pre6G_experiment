/* Local presentation only: no requests, timers, job submission or telemetry. */
const scenarios = {
  profiling: {
    label: '分析中', status: '正在分析候選節點', tone: 'neutral', step: 1,
    kicker: '節點分析', badge: '分析 2 個候選節點', title: '正在為你的任務尋找合適的節點',
    description: '平台正在短暫試跑任務，比較每個節點的預測訓練時間與能耗。完成分析後，這裡會顯示配置結果。',
    node: '尚未選定', nodeDetail: 'RTX4090 / RTX5090 分析中', machineState: '等待分析結果', predictions: false,
    executionTitle: '正在分析候選節點', executionDescription: 'RTX4090 分析已完成，正在等待 RTX5090 的分析結果。',
    progressTag: '分析中', progressLabel: '已完成節點分析', progressValue: '1 / 2 個節點', progress: 50,
    elapsed: '分析已經過 01:12', remaining: '任務訓練尚未開始', choice: '分析完成', otherChoice: '分析中',
    gpu: 28, memory: 34, jobs: 1, loadState: '分析完成',
    events: [['14:31:12', 'RTX4090 分析完成', '正在等待另一候選節點。'], ['14:30:08', '開始節點分析', '候選：RTX4090、RTX5090。'], ['14:30:00', '任務已接收', '已確認 960 次訓練迭代。']]
  },
  selected: {
    label: '已選點', status: '已選定節點', tone: 'green', step: 2,
    kicker: '配置結果', badge: '符合節能偏好', title: '已為你選擇 RTX4090',
    description: '此任務在 RTX4090 的預測訓練時間與能耗較低，平台依「節能優先」偏好選擇此節點。',
    node: 'iccl-s3-251230', nodeDetail: 'RTX4090 · 共享 GPU 節點', machineState: '等待任務啟動', predictions: true,
    executionTitle: '接下來：啟動訓練', executionDescription: '節點配置已完成，任務即將在 RTX4090 上啟動。',
    progressTag: '等待執行', progressLabel: '訓練進度', progressValue: '0 / 960 次迭代', progress: 0,
    elapsed: '尚未開始', remaining: '預測 steady training 60.8 秒', choice: '✓ 已選擇', otherChoice: '未選擇',
    gpu: 28, memory: 34, jobs: 1, loadState: '待執行',
    events: [['14:32:04', '已選擇 RTX4090', '依預測能耗與節能偏好配置。'], ['14:32:02', '節點分析完成', '2 個候選節點已完成比較。'], ['14:30:00', '任務已接收', '已確認 960 次訓練迭代。']]
  },
  running: {
    label: '執行中', status: '任務執行中', tone: 'green', step: 3,
    kicker: '執行節點', badge: '任務執行中', title: '你的任務正在 RTX4090 上執行',
    description: '平台已依節能偏好配置節點。下方可查看訓練進度與當前節點負載。',
    node: 'iccl-s3-251230', nodeDetail: 'RTX4090 · 本任務使用 1 份共享 GPU 配額', machineState: '執行中', predictions: true,
    executionTitle: '模型訓練進行中', executionDescription: '已完成 18 / 30 個 epochs，任務持續執行中。',
    progressTag: '60% 完成', progressLabel: '訓練進度', progressValue: '576 / 960 次迭代', progress: 60,
    elapsed: '訓練已經過 00:37', remaining: '預計剩餘訓練約 24 秒', choice: '✓ 執行節點', otherChoice: '未選擇',
    gpu: 76, memory: 68, jobs: 2, loadState: '含本任務',
    events: [['14:32:44', '訓練進度 60%', '已完成 576 次迭代。'], ['14:32:07', '訓練開始', '任務已在 RTX4090 啟動。'], ['14:32:04', '已選擇 RTX4090', '依預測能耗與節能偏好配置。'], ['14:30:00', '任務已接收', '已確認 960 次訓練迭代。']]
  },
  completed: {
    label: '已完成', status: '任務已完成', tone: 'green', step: 4,
    kicker: '執行結果', badge: '訓練完成', title: '你的訓練任務已完成',
    description: '此任務已在 RTX4090 完成訓練、驗證與成果存檔。資源配額已釋放，可查看成果紀錄。',
    node: 'iccl-s3-251230', nodeDetail: 'RTX4090 · 任務使用的節點', machineState: '配額已釋放', predictions: true,
    executionTitle: '訓練與成果存檔完成', executionDescription: '30 / 30 個 epochs 已完成。以下耗時為介面示意，非本專案的 production 實測。',
    progressTag: '100% 完成', progressLabel: '訓練進度', progressValue: '960 / 960 次迭代', progress: 100,
    elapsed: '示意訓練耗時 01:03', remaining: '示意任務執行耗時 01:18（含啟動與存檔）', choice: '✓ 執行節點', otherChoice: '未選擇',
    gpu: 28, memory: 34, jobs: 1, loadState: '本任務已結束',
    events: [['14:33:22', '成果已存檔，配額已釋放', 'best.pt 與訓練摘要已就緒。'], ['14:33:10', '訓練完成', '960 / 960 次迭代。'], ['14:32:07', '訓練開始', '任務已在 RTX4090 啟動。'], ['14:32:04', '任務開始配置', '選定 RTX4090。']]
  },
  blocked: {
    label: '待確認', status: '需要確認', tone: 'amber', step: 2,
    kicker: '配置暫停', badge: '預測可靠性待確認', title: '目前無法可靠地選擇節點',
    description: '目前節點的部分量測值超出模型已驗證範圍，平台已暫停自動配置，等待管理員確認。',
    node: '尚未配置節點', nodeDetail: '任務已保留，不佔用訓練配額', machineState: '等待管理員處理', predictions: false,
    executionTitle: '任務尚未開始', executionDescription: '預測結果未通過品質檢查。確認完成後，平台才會繼續配置與執行。',
    progressTag: '待確認', progressLabel: '訓練進度', progressValue: '0 / 960 次迭代', progress: 0,
    elapsed: '訓練尚未開始', remaining: '目前無可用完成時間估計', choice: '待確認', otherChoice: '待確認',
    gpu: 28, memory: 34, jobs: 1, loadState: '未配置本任務',
    events: [['14:32:04', '自動配置暫停', '節點量測超出模型已驗證範圍。'], ['14:32:02', '候選節點分析完成', '預測結果需要進一步確認。'], ['14:30:00', '任務已接收', '任務設定與資料需求已保留。']]
  }
};

function showScenario(key) {
  const state = scenarios[key] || scenarios.selected;
  const text = (id, value) => { document.getElementById(id).textContent = value; };
  document.querySelectorAll('[data-scenario]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.scenario === key)));
  const fields = {
    'task-status': state.status, 'decision-kicker': state.kicker, 'decision-badge': state.badge,
    'decision-heading': state.title, 'decision-description': state.description,
    'selected-node': state.node, 'selected-node-detail': state.nodeDetail, 'machine-state': state.machineState,
    'execution-heading': state.executionTitle, 'execution-description': state.executionDescription,
    'progress-tag': state.progressTag, 'progress-label': state.progressLabel, 'progress-value': state.progressValue,
    'elapsed-label': state.elapsed, 'remaining-label': state.remaining, 'choice-4090': state.choice,
    'choice-5090': state.otherChoice, 'gpu-load-4090': `${state.gpu}%`, 'memory-4090': `${state.memory}%`,
    'jobs-4090': `${state.jobs} 個`, 'load-state-4090': state.loadState,
    'scenario-announcement': `展示情境：${state.label}。${state.title}。`
  };
  Object.entries(fields).forEach(([id, value]) => text(id, value));
  ['task-status', 'decision-badge'].forEach(id => { document.getElementById(id).className = `badge ${state.tone}`; });
  document.querySelectorAll('[data-step]').forEach(item => {
    const step = Number(item.dataset.step);
    item.className = step < state.step ? 'done' : step === state.step ? 'current' : '';
    if (key === 'blocked' && step === state.step) item.classList.add('attention');
    if (step === state.step) item.setAttribute('aria-current', 'step'); else item.removeAttribute('aria-current');
    item.querySelector('.step-circle').textContent = step < state.step || (key === 'completed' && step === 4) ? '✓' : key === 'blocked' && step === 2 ? '!' : String(step + 1);
  });
  const progress = document.getElementById('task-progress');
  progress.setAttribute('aria-valuenow', String(state.progress));
  progress.setAttribute('aria-label', `示意${state.progressLabel}`);
  progress.setAttribute('aria-valuetext', state.progressValue);
  progress.classList.toggle('attention', key === 'blocked');
  document.getElementById('progress-fill').style.width = `${state.progress}%`;
  document.getElementById('result-notice').hidden = key !== 'completed';
  const meter = document.getElementById('gpu-meter-4090');
  meter.value = state.gpu;
  meter.textContent = `${state.gpu}%`;
  text('predicted-time', state.predictions ? '60.8 秒' : '—');
  text('predicted-energy', state.predictions ? '21.54 kJ' : '—');
  text('predicted-saving', state.predictions ? '−62.3%' : '—');
  document.querySelectorAll('[data-prediction]').forEach(cell => { cell.textContent = state.predictions ? cell.dataset.prediction : '—'; });
  document.getElementById('choice-4090').className = state.predictions ? 'choice-label' : '';
  const activity = document.getElementById('activity-list');
  activity.replaceChildren(...state.events.map(([stamp, title, detail]) => {
    const item = document.createElement('li');
    const time = document.createElement('time'); time.textContent = stamp; time.dateTime = stamp;
    const heading = document.createElement('strong'); heading.textContent = title;
    const paragraph = document.createElement('p'); paragraph.textContent = detail;
    item.append(time, heading, paragraph);
    return item;
  }));
}

document.querySelectorAll('[data-scenario]').forEach(button => button.addEventListener('click', () => showScenario(button.dataset.scenario)));
showScenario('selected');
