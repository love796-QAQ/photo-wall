(function () {
  'use strict';

  var state = {
    groups: [],
    activeGroupId: null,
    selectedGroupId: null,
    selectedGroup: null,
    pendingFiles: []
  };
  var $ = function (selector) { return document.querySelector(selector); };

  async function api(url, options) {
    var response = await fetch(url, options);
    var payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || '请求失败');
    return payload;
  }

  async function loadGroups(selectActive) {
    var payload = await api('/api/groups');
    state.groups = payload.groups;
    state.activeGroupId = payload.active_group_id;
    if (selectActive || !state.groups.some(function (group) { return group.id === state.selectedGroupId; })) {
      state.selectedGroupId = state.activeGroupId || (state.groups[0] && state.groups[0].id);
    }
    renderGroups();
    if (state.selectedGroupId) await loadGroup(state.selectedGroupId);
    else renderEmpty();
  }

  function renderGroups() {
    $('#groups-list').innerHTML = state.groups.map(function (group) {
      return '<button class="group-card' +
        (group.id === state.activeGroupId ? ' active' : '') +
        (group.id === state.selectedGroupId ? ' selected' : '') +
        '" data-group-id="' + group.id + '">' +
        (group.cover ? '<img class="group-cover" src="' + escapeHtml(group.cover) + '" alt="">' : '<div class="group-cover"></div>') +
        '<span><strong>' + escapeHtml(group.name) + '</strong><small>' + group.photo_count + ' 张照片</small></span>' +
        (group.id === state.activeGroupId ? '<em>首页展示</em>' : '') +
      '</button>';
    }).join('');
  }

  async function loadGroup(groupId) {
    var payload = await api('/api/groups/' + groupId);
    state.selectedGroup = payload.group;
    state.selectedGroupId = groupId;
    renderGroups();
    renderGroup();
  }

  function renderGroup() {
    var group = state.selectedGroup;
    $('#detail-name').textContent = group.name;
    $('#detail-count').textContent = group.photos.length + ' PHOTOS';
    $('#rename-group-button').disabled = false;
    $('#delete-group-button').disabled = false;
    var displayButton = $('#select-display-button');
    displayButton.disabled = group.id === state.activeGroupId;
    displayButton.textContent = group.id === state.activeGroupId ? '首页展示中' : '设为首页展示';
    $('#group-detail').innerHTML = '<div class="photo-grid">' +
      group.photos.map(function (photo) {
        var isCover = photo.id === group.cover_photo_id;
        var isStoryCover = photo.id === group.story_cover_photo_id;
        var isDetailVisible =
          (isCover && group.show_cover_in_details) ||
          (isStoryCover && group.show_story_cover_in_details);
        return '<article class="photo-card' + (isCover || isStoryCover ? ' cover' : '') + '">' +
          '<img src="' + escapeHtml(photo.path) + '" alt="">' +
          '<div class="role-badges">' +
            (isCover ? '<span class="cover-badge">HOME</span>' : '') +
            (isStoryCover ? '<span class="cover-badge story">STORY</span>' : '') +
          '</div>' +
          '<div class="role-visibility">' +
            ((isCover || isStoryCover)
              ? '<button class="visibility-chip' + (isDetailVisible ? ' visible' : '') + '" type="button" data-toggle-photo-visibility="' + photo.id + '" data-home-role="' + isCover + '" data-story-role="' + isStoryCover + '">' +
                (isDetailVisible ? '详情可见' : '仅作封面') +
              '</button>'
              : '') +
          '</div>' +
          '<div class="photo-actions">' +
            (!isCover ? '<button type="button" data-cover-photo="' + photo.id + '">设为首页封面</button>' : '') +
            (!isStoryCover ? '<button type="button" data-story-cover-photo="' + photo.id + '">设为叙事封面</button>' : '') +
            '<button class="danger" type="button" data-delete-photo="' + photo.id + '">删除照片</button>' +
          '</div>' +
          '<div class="photo-card-info"><strong>' + escapeHtml(photo.location || photo.name) + '</strong>' +
          '<small>' + escapeHtml([photo.date, photo.camera].filter(Boolean).join(' · ') || '暂无 EXIF 信息') + '</small></div>' +
        '</article>';
      }).join('') +
      '<button class="add-card" id="add-photos-card" type="button"><div><span>＋</span><strong>添加照片</strong></div></button>' +
    '</div>';
  }

  function renderEmpty() {
    state.selectedGroup = null;
    $('#detail-name').textContent = '请选择分组';
    $('#detail-count').textContent = '0 PHOTOS';
    $('#select-display-button').disabled = true;
    $('#rename-group-button').disabled = true;
    $('#delete-group-button').disabled = true;
    $('#group-detail').innerHTML = '<div class="empty-state">新建或选择一个分组</div>';
  }

  async function createGroup(name) {
    var payload = await api('/api/groups', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name })
    });
    state.selectedGroupId = payload.group.id;
    await loadGroups(false);
    toast('分组已创建，可以点击加号添加照片');
  }

  async function selectDisplayGroup() {
    await api('/api/groups/select', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ group_id: state.selectedGroupId })
    });
    state.activeGroupId = state.selectedGroupId;
    renderGroups();
    renderGroup();
    toast('已切换首页展示分组');
  }

  async function renameGroup(name) {
    await api('/api/groups/rename', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ group_id: state.selectedGroupId, name: name })
    });
    await loadGroups(false);
    toast('分组名称已更新');
  }

  async function deleteGroup() {
    if (!state.selectedGroup) return;
    var name = state.selectedGroup.name;
    if (!window.confirm('确定删除分组“' + name + '”及其中的全部照片吗？此操作无法撤销。')) return;
    await api('/api/groups/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ group_id: state.selectedGroupId })
    });
    state.selectedGroupId = null;
    await loadGroups(true);
    toast('分组已删除');
  }

  async function deletePhoto(photoId) {
    var photo = state.selectedGroup.photos.find(function (item) { return item.id === photoId; });
    if (!photo || !window.confirm('确定删除照片“' + (photo.location || photo.name) + '”吗？')) return;
    await api('/api/photos/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ group_id: state.selectedGroupId, photo_id: photoId })
    });
    await loadGroup(state.selectedGroupId);
    await refreshGroupSummary();
    toast('照片已删除');
  }

  async function setCover(photoId) {
    await api('/api/groups/cover', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ group_id: state.selectedGroupId, photo_id: photoId })
    });
    await loadGroup(state.selectedGroupId);
    await refreshGroupSummary();
    toast('封面已更新');
  }

  async function setStoryCover(photoId) {
    await api('/api/groups/story-cover', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ group_id: state.selectedGroupId, photo_id: photoId })
    });
    await loadGroup(state.selectedGroupId);
    toast('叙事封面已更新');
  }

  async function saveDetailSettings(nextHome, nextStory) {
    if (!state.selectedGroupId) return;
    var payload = await api('/api/groups/detail-settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        group_id: state.selectedGroupId,
        show_cover_in_details: nextHome,
        show_story_cover_in_details: nextStory
      })
    });
    state.selectedGroup.show_cover_in_details = payload.show_cover_in_details;
    state.selectedGroup.show_story_cover_in_details = payload.show_story_cover_in_details;
    renderGroup();
    toast('详情展示设置已保存');
  }

  async function refreshGroupSummary() {
    var payload = await api('/api/groups');
    state.groups = payload.groups;
    state.activeGroupId = payload.active_group_id;
    renderGroups();
  }

  function openUpload(files) {
    if (!state.selectedGroup || !files.length) return;
    state.pendingFiles = Array.from(files);
    $('#upload-group-name').textContent = state.selectedGroup.name;
    $('#upload-file-summary').innerHTML = '<strong>' + state.pendingFiles.length + ' 个文件</strong><br>' +
      state.pendingFiles.slice(0, 5).map(function (file) { return escapeHtml(file.name); }).join('<br>') +
      (state.pendingFiles.length > 5 ? '<br>…' : '');
    $('#upload-modal').showModal();
  }

  async function uploadPhotos() {
    var data = new FormData();
    data.append('group_id', state.selectedGroupId);
    state.pendingFiles.forEach(function (file) { data.append('photos', file); });
    data.append('resolve_locations', String($('#upload-resolve').checked));
    var payload = await api('/api/upload/photos', { method: 'POST', body: data });
    $('#upload-modal').close();
    state.pendingFiles = [];
    await loadGroups(false);
    toast('已添加 ' + payload.uploaded + ' 张照片');
  }

  async function importZip() {
    var data = new FormData();
    data.append('group_name', $('#zip-group-name').value);
    data.append('archive', $('#zip-file').files[0]);
    data.append('resolve_locations', String($('#zip-resolve').checked));
    var payload = await api('/api/upload/zip', { method: 'POST', body: data });
    state.selectedGroupId = payload.group_id;
    $('#zip-modal').close();
    $('#zip-form').reset();
    await loadGroups(false);
    toast('压缩包已导入，共 ' + payload.uploaded + ' 张照片');
  }

  function runBusy(form, task) {
    return async function (event) {
      event.preventDefault();
      var button = form.querySelector('.primary-button');
      var label = button.textContent;
      button.disabled = true;
      button.textContent = '处理中...';
      try { await task(); }
      catch (error) { toast(error.message, true); }
      finally { button.disabled = false; button.textContent = label; }
    };
  }

  function bindModal(modal) {
    modal.querySelectorAll('[data-close]').forEach(function (button) {
      button.addEventListener('click', function () { modal.close(); });
    });
    modal.addEventListener('click', function (event) {
      if (event.target === modal) modal.close();
    });
  }

  function toast(message, error) {
    var element = $('#toast');
    element.textContent = message;
    element.classList.toggle('error', Boolean(error));
    element.classList.add('show');
    clearTimeout(toast.timer);
    toast.timer = setTimeout(function () { element.classList.remove('show'); }, 3200);
  }

  function escapeHtml(value) {
    var div = document.createElement('div');
    div.textContent = value == null ? '' : String(value);
    return div.innerHTML;
  }

  $('#groups-list').addEventListener('click', function (event) {
    var card = event.target.closest('[data-group-id]');
    if (card) loadGroup(card.dataset.groupId).catch(function (error) { toast(error.message, true); });
  });
  $('#group-detail').addEventListener('click', function (event) {
    var coverButton = event.target.closest('[data-cover-photo]');
    if (coverButton) {
      setCover(coverButton.dataset.coverPhoto).catch(function (error) { toast(error.message, true); });
      return;
    }
    var storyCoverButton = event.target.closest('[data-story-cover-photo]');
    if (storyCoverButton) {
      setStoryCover(storyCoverButton.dataset.storyCoverPhoto).catch(function (error) { toast(error.message, true); });
      return;
    }
    var visibilityToggle = event.target.closest('[data-toggle-photo-visibility]');
    if (visibilityToggle) {
      var controlsHome = visibilityToggle.dataset.homeRole === 'true';
      var controlsStory = visibilityToggle.dataset.storyRole === 'true';
      var currentlyVisible =
        (controlsHome && state.selectedGroup.show_cover_in_details) ||
        (controlsStory && state.selectedGroup.show_story_cover_in_details);
      var nextVisible = !currentlyVisible;
      saveDetailSettings(
        controlsHome ? nextVisible : state.selectedGroup.show_cover_in_details,
        controlsStory ? nextVisible : state.selectedGroup.show_story_cover_in_details
      ).catch(function (error) { toast(error.message, true); });
      return;
    }
    var deleteButton = event.target.closest('[data-delete-photo]');
    if (deleteButton) {
      deletePhoto(deleteButton.dataset.deletePhoto).catch(function (error) { toast(error.message, true); });
      return;
    }
    if (event.target.closest('#add-photos-card')) $('#append-files').click();
  });
  $('#append-files').addEventListener('change', function () {
    openUpload(this.files);
    this.value = '';
  });
  $('#new-group-button').addEventListener('click', function () { $('#new-group-modal').showModal(); });
  $('#import-zip-button').addEventListener('click', function () { $('#zip-modal').showModal(); });
  $('#refresh-groups').addEventListener('click', function () { loadGroups(false).catch(function (error) { toast(error.message, true); }); });
  $('#select-display-button').addEventListener('click', function () { selectDisplayGroup().catch(function (error) { toast(error.message, true); }); });
  $('#rename-group-button').addEventListener('click', function () {
    if (!state.selectedGroup) return;
    $('#rename-group-name').value = state.selectedGroup.name;
    $('#rename-group-modal').showModal();
    $('#rename-group-name').focus();
    $('#rename-group-name').select();
  });
  $('#delete-group-button').addEventListener('click', function () {
    deleteGroup().catch(function (error) { toast(error.message, true); });
  });

  $('#new-group-form').addEventListener('submit', runBusy($('#new-group-form'), async function () {
    await createGroup($('#new-group-name').value.trim());
    $('#new-group-modal').close();
    $('#new-group-form').reset();
  }));
  $('#zip-form').addEventListener('submit', runBusy($('#zip-form'), importZip));
  $('#upload-form').addEventListener('submit', runBusy($('#upload-form'), uploadPhotos));
  $('#rename-group-form').addEventListener('submit', runBusy($('#rename-group-form'), async function () {
    await renameGroup($('#rename-group-name').value.trim());
    $('#rename-group-modal').close();
  }));

  document.querySelectorAll('.modal').forEach(bindModal);
  loadGroups(true).catch(function (error) { toast(error.message, true); });
})();
