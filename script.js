(function () {
  'use strict';

  var COPY = {
    TITLE_FALLBACK: '照片墙',
    UNTITLED: 'UNTITLED',
    UNKNOWN_DATE: 'DATE UNKNOWN',
    UNKNOWN_CAMERA: 'UNKNOWN CAMERA',
    EMPTY_NOTE: '暂无相册内容',
    STORY_INTRO: '照片没有解释当时发生了什么，它只保留光线、距离和按下快门的那一刻。',
    FRAME_NOTES: 'FRAME NOTES',
    CAPTION_SUFFIX: '，与上一帧共同构成这一段记忆。',
    TIME_UNRECORDED: '时间未记录',
    ERROR_LOAD: '无法加载照片数据，请刷新页面重试。',
    QUOTES: [
      '城市很吵，记忆却总是无声。',
      '那天的光线，比日期更容易被想起。',
      '走过以后，风景才有了名字。',
      '快门落下，时间短暂停止。',
      '远方不是地点，是一段正在发生的生活。',
      '有些画面，后来成了答案。',
      '我们终究会回到这些瞬间。'
    ],
    INTRO_QUOTES: [
      '光影交错，时间在此停留。',
      '有些瞬间，只发生一次。',
      '我们拍下的，也是我们成为的。',
      '记忆会模糊，影像不会。'
    ]
  };

  var photos = [];
  var archivePhotos = [];
  var coverPhoto = null;
  var storyCoverPhoto = null;
  var availableGroups = [];
  var defaultGroupId = null;
  var currentGroupId = null;
  var currentGroupName = COPY.TITLE_FALLBACK;
  var qs = function (selector, scope) { return (scope || document).querySelector(selector); };
  var qsa = function (selector, scope) { return Array.from((scope || document).querySelectorAll(selector)); };
  var reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var lightboxIndex = -1;
  var ticking = false;

  var elements = {
    loader: qs('#loader'),
    loaderBar: qs('#loader-bar'),
    loaderValue: qs('#loader-value'),
    hero: qs('#hero-mask'),
    heroImage: qs('#hm-bg-img'),
    heroCutout: qs('#hm-cutout'),
    heroMaskWords: qs('#hm-mask-words'),
    heroFillWords: qs('#hm-fill-words'),
    heroMaskBase: qs('#hm-mask-base'),
    releasePanel: qs('#release-panel'),
    emptyHero: qs('#empty-hero'),
    scrollCue: qs('.scroll-cue'),
    storyCover: qs('#story-cover'),
    stories: qs('#photo-sections'),
    stripTrack: qs('#strip-track'),
    stripViewport: qs('#strip-viewport'),
    menuButton: qs('#menu-button'),
    menuPanel: qs('#menu-panel'),
    menuGroups: qs('#menu-groups'),
    menuList: qs('#menu-list'),
    siteHeader: qs('#site-header'),
    headerCurrent: qs('#header-current'),
    headerTotal: qs('#header-total'),
    releaseCount: qs('#release-count'),
    lightbox: qs('#lightbox')
  };

  async function init() {
    var ok = false;
    try {
      var responses = await Promise.all([
        fetch('/api/groups', { cache: 'no-store' }),
        fetch('/api/active-photos', { cache: 'no-store' })
      ]);
      var groupsPayload = await responses[0].json();
      var payload = await responses[1].json();
      availableGroups = groupsPayload.groups || [];
      defaultGroupId = groupsPayload.active_group_id;
      applyGroupPayload(payload);
      ok = true;
    } catch (error) {
      console.error('Photo Wall init failed:', error);
    }

    if (!ok || !archivePhotos.length) {
      showErrorState(ok ? null : COPY.ERROR_LOAD);
      return;
    }

    setCounts();
    buildHero();
    buildStories();
    buildArchive();
    buildGroupMenu();
    buildMenu();
    setupControls();
    setupReveal();
    preloadOpeningImages();
    requestTick();
  }

  function showErrorState(message) {
    if (message) {
      document.body.classList.add('empty-state');
      if (elements.emptyHero) {
        elements.emptyHero.setAttribute('aria-hidden', 'false');
        var h1 = elements.emptyHero.querySelector('h1');
        var p = elements.emptyHero.querySelector('p');
        if (h1) h1.textContent = '出错了';
        if (p) p.textContent = message;
      }
      if (elements.releaseCount) elements.releaseCount.textContent = '00 FRAMES';
      if (elements.stories) elements.stories.innerHTML = '';
      if (elements.stripTrack) elements.stripTrack.innerHTML = '';
      if (elements.loaderValue) elements.loaderValue.textContent = '00';
      if (elements.loaderBar) elements.loaderBar.style.width = '100%';
      if (elements.loader) setTimeout(function () { elements.loader.classList.add('hidden'); }, 180);
      return;
    }
    buildEmptyState();
  }

  function applyGroupPayload(payload) {
    var group = payload.group;
    photos = group ? group.photos : [];
    archivePhotos = group ? group.all_photos : photos;
    coverPhoto = group ? group.cover_photo : photos[0];
    storyCoverPhoto = group ? group.story_cover_photo : photos[Math.min(1, photos.length - 1)];
    currentGroupId = group ? group.id : null;
    currentGroupName = group ? group.name : COPY.TITLE_FALLBACK;
    qsa('.hm-svg-title').forEach(function (title) {
      title.textContent = currentGroupName;
    });
    elements.heroCutout.setAttribute('aria-label', currentGroupName + ' Photo Wall');
    document.title = group ? group.name + ' — PHOTO WALL' : 'PHOTO WALL';
  }

  function setCounts() {
    var total = String(archivePhotos.length).padStart(2, '0');
    if (elements.releaseCount) elements.releaseCount.textContent = total + ' FRAMES';
  }

  function buildHero() {
    var heroPhoto = coverPhoto || archivePhotos[0];
    var narrativePhoto = storyCoverPhoto || archivePhotos[Math.min(1, archivePhotos.length - 1)];
    elements.heroImage.src = heroPhoto.path;
    elements.heroImage.alt = heroPhoto.location || heroPhoto.name;
    layoutHeroMask();
    elements.storyCover.src = narrativePhoto.path;
    elements.storyCover.alt = narrativePhoto.location || narrativePhoto.name;
    var introEl = qs('#intro-quote');
    if (introEl) introEl.textContent = COPY.INTRO_QUOTES[Math.floor(Math.random() * COPY.INTRO_QUOTES.length)];
  }

  function buildEmptyState() {
    document.body.classList.add('empty-state');
    if (elements.heroImage) {
      elements.heroImage.removeAttribute('src');
      elements.heroImage.alt = '';
    }
    if (elements.storyCover) {
      elements.storyCover.removeAttribute('src');
      elements.storyCover.alt = '';
    }
    if (elements.emptyHero) elements.emptyHero.setAttribute('aria-hidden', 'false');
    if (elements.releaseCount) elements.releaseCount.textContent = '00 FRAMES';
    if (elements.headerCurrent) elements.headerCurrent.textContent = '00';
    if (elements.headerTotal) elements.headerTotal.textContent = '00';
    if (elements.stories) elements.stories.innerHTML = '';
    if (elements.stripTrack) elements.stripTrack.innerHTML = '';
    buildGroupMenu();
    elements.menuList.innerHTML = '<span class="menu-empty-note">' + COPY.EMPTY_NOTE + '</span>';
    setupControls();
    if (elements.loaderValue) elements.loaderValue.textContent = '00';
    if (elements.loaderBar) elements.loaderBar.style.width = '100%';
    if (elements.loader) setTimeout(function () { elements.loader.classList.add('hidden'); }, 180);
  }

  function buildStories() {
    var groups = [];
    for (var i = 0; i < photos.length; i += 2) {
      groups.push([photos[i], photos[(i + 1) % photos.length]]);
    }

    elements.stories.innerHTML = groups.map(function (group, index) {
      var primary = group[0];
      var secondary = group[1];
      var place = splitLocation(primary.location);
      var title = place.title || cleanName(primary.name);
      var date = primary.date || COPY.UNKNOWN_DATE;
      var camera = primary.camera || COPY.UNKNOWN_CAMERA;
      var titleClass = title.length > 6 ? ' long' : '';

      return [
        '<article class="photo-story" id="story-', index, '" data-story-index="', index, '">',
          '<div class="story-heading reveal">',
            '<span class="story-number">MEMORY ', String(index + 1).padStart(2, '0'), '</span>',
            '<h2 class="story-place-title', titleClass, '">', esc(title), '</h2>',
            place.detail ? '<p class="story-place-detail">' + esc(place.detail) + '</p>' : '',
            '<p class="story-date">', esc(date), ' · ', esc(camera), '</p>',
            '<p class="story-quote">', esc(storyLine(index)), '</p>',
            '<p class="story-copy">', COPY.STORY_INTRO, '</p>',
          '</div>',
          mediaMarkup(primary, 'story-media-primary'),
          mediaMarkup(secondary, 'story-media-secondary'),
          '<div class="story-caption reveal">',
            '<p class="eyebrow">', COPY.FRAME_NOTES, '</p>',
            '<p>', esc(secondary.location || cleanName(secondary.name)), '。', esc(secondary.date || COPY.TIME_UNRECORDED), COPY.CAPTION_SUFFIX, '</p>',
          '</div>',
        '</article>'
      ].join('');
    }).join('');

    if (elements.headerCurrent) elements.headerCurrent.textContent = groups.length ? '01' : '00';
    if (elements.headerTotal) elements.headerTotal.textContent = String(groups.length).padStart(2, '0');
  }

  function mediaMarkup(photo, className) {
    var photoIndex = archivePhotos.findIndex(function (item) {
      return item.id && photo.id ? item.id === photo.id : item.path === photo.path;
    });
    return [
      '<button class="story-media parallax-media ', className, '" type="button" data-photo-index="', photoIndex, '" aria-label="查看 ', esc(photo.name), '">',
        '<img src="', attr(photo.path), '" alt="', attr(photo.location || photo.name), '" loading="lazy">',
      '</button>'
    ].join('');
  }

  function buildArchive() {
    elements.stripTrack.innerHTML = archivePhotos.map(function (photo, index) {
      return [
        '<button class="strip-item" type="button" data-photo-index="', index, '" aria-label="查看 ', esc(photo.name), '">',
          '<img src="', attr(photo.path), '" alt="', attr(photo.location || photo.name), '" loading="lazy">',
          '<span class="strip-item-label">', esc(photo.location || cleanName(photo.name)), '</span>',
        '</button>'
      ].join('');
    }).join('');
  }

  function buildMenu() {
    var stories = qsa('.photo-story');
    elements.menuList.innerHTML = stories.map(function (story, index) {
      var photo = photos[index * 2];
      return [
        '<button type="button" data-target="story-', index, '">',
          '<span>', esc(photo.location || cleanName(photo.name)), '</span>',
          '<small>', String(index + 1).padStart(2, '0'), '</small>',
        '</button>'
      ].join('');
    }).join('');
  }

  function buildGroupMenu() {
    elements.menuGroups.innerHTML = availableGroups.map(function (group) {
      var isCurrent = group.id === currentGroupId;
      var isDefault = group.id === defaultGroupId;
      return [
        '<button class="menu-group', isCurrent ? ' current' : '', '" type="button" data-browse-group="', attr(group.id), '"', group.photo_count ? '' : ' disabled', '>',
          group.cover
            ? '<img src="' + attr(group.cover) + '" alt="">'
            : '<span class="menu-group-cover"></span>',
          '<span><strong>', esc(group.name), '</strong><small>', group.photo_count, ' PHOTOS', isCurrent ? ' · 当前浏览' : '', '</small></span>',
          isDefault ? '<em>默认打开</em>' : '',
        '</button>'
      ].join('');
    }).join('');
  }

  async function switchGroup(groupId) {
    if (!groupId || groupId === currentGroupId) {
      closeMenu();
      return;
    }

    elements.menuPanel.classList.add('switching');
    try {
      var response = await fetch('/api/active-photos?group_id=' + encodeURIComponent(groupId), { cache: 'no-store' });
      var payload = await response.json();
      if (!response.ok || !payload.ok || !payload.group) throw new Error(payload.error || '分组加载失败');

      applyGroupPayload(payload);
      setCounts();
      buildHero();
      buildStories();
      buildArchive();
      buildGroupMenu();
      buildMenu();
      setupReveal();
      closeLightbox();
      closeMenu();
      elements.stripViewport.scrollLeft = 0;
      window.scrollTo({ top: 0, behavior: 'auto' });
      requestTick();
    } catch (error) {
      elements.menuPanel.classList.remove('switching');
      console.error(error);
    }
  }

  function setupControls() {
    window.addEventListener('scroll', requestTick, { passive: true });
    window.addEventListener('resize', function () {
      layoutHeroMask();
      requestTick();
    }, { passive: true });

    qs('#back-top').addEventListener('click', function () {
      window.scrollTo({ top: 0, behavior: reducedMotion ? 'auto' : 'smooth' });
    });

    elements.menuButton.addEventListener('click', toggleMenu);
    elements.menuGroups.addEventListener('click', function (event) {
      var button = event.target.closest('[data-browse-group]');
      if (button && !button.disabled) switchGroup(button.dataset.browseGroup);
    });
    elements.menuList.addEventListener('click', function (event) {
      var button = event.target.closest('[data-target]');
      if (!button) return;
      closeMenu();
      var target = document.getElementById(button.dataset.target);
      if (target) target.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth' });
    });

    document.addEventListener('click', function (event) {
      var target = event.target.closest('[data-photo-index]');
      if (target) openLightbox(Number(target.dataset.photoIndex));
    });

    qs('#lightbox-close').addEventListener('click', closeLightbox);
    qs('.lightbox-overlay').addEventListener('click', closeLightbox);
    qs('#lightbox-prev').addEventListener('click', function () { navigateLightbox(-1); });
    qs('#lightbox-next').addEventListener('click', function () { navigateLightbox(1); });

    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') {
        if (elements.lightbox.classList.contains('active')) closeLightbox();
        else closeMenu();
      }
      if (!elements.lightbox.classList.contains('active')) return;
      if (event.key === 'ArrowLeft') navigateLightbox(-1);
      if (event.key === 'ArrowRight') navigateLightbox(1);
    });

    setupDragScroll();
    setupLightboxSwipe();
    updateHeaderCounterVisibility();
  }

  function requestTick() {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(function () {
      updateHero();
      updateParallax();
      updateHeaderCounterVisibility();
      ticking = false;
    });
  }

  function updateHero() {
    var range = Math.max(1, qs('.hm-wrapper').offsetHeight - window.innerHeight);
    var progress = clamp(window.scrollY / range, 0, 1);
    var maskStart = .06;
    var maskProgress = clamp((progress - maskStart) / .16, 0, 1);
    var maskReveal = smoothstep(maskProgress);
    var scaleProgress = easeOutCubic(clamp((progress - maskStart) / .7, 0, 1));
    var initialScale = window.innerWidth < 800 ? 15 : 19;
    var logoScale = mix(initialScale, 1, scaleProgress);
    var fill = clamp((progress - 0.5) / 0.18, 0, 1);
    var coverFade = smoothstep(clamp((progress - .48) / .2, 0, 1));
    var handoff = smoothstep(clamp((progress - .7) / .18, 0, 1));
    var releaseEnter = smoothstep(clamp((progress - .73) / .18, 0, 1));
    var cx = window.innerWidth / 2;
    var cy = window.innerHeight / 2;
    var finalLogoScale = window.innerWidth < 800 ? .72 : .58;
    var handoffScale = mix(1, finalLogoScale, handoff);
    var logoX = mix(cx, window.innerWidth < 800 ? window.innerWidth * .18 : window.innerWidth * .13, handoff);
    var logoY = mix(cy, window.innerHeight < 700 ? 105 : 125, handoff);
    var transform = 'translate(' + logoX + ' ' + logoY + ') scale(' + (logoScale * handoffScale) + ') translate(' + (-cx) + ' ' + (-cy) + ')';

    elements.heroMaskWords.setAttribute('transform', transform);
    elements.heroFillWords.setAttribute('transform', transform);
    elements.heroCutout.style.opacity = maskReveal;
    elements.heroFillWords.style.opacity = fill * (1 - handoff * .35);
    elements.hero.style.setProperty('--cover-opacity', 1 - coverFade);
    elements.releasePanel.style.opacity = releaseEnter;
    elements.releasePanel.style.setProperty('--release-y', mix(10, 0, releaseEnter) + 'vh');
    elements.releasePanel.style.setProperty('--release-scale', mix(.94, 1, releaseEnter));
    elements.scrollCue.style.opacity = clamp(1 - progress * 4, 0, 1);
  }

  function updateParallax() {
    if (reducedMotion) return;
    qsa('.parallax-media').forEach(function (media) {
      var rect = media.getBoundingClientRect();
      if (rect.bottom < 0 || rect.top > window.innerHeight) return;
      var progress = (window.innerHeight - rect.top) / (window.innerHeight + rect.height);
      media.style.setProperty('--media-y', mix(-4, 0, clamp(progress, 0, 1)) + '%');
    });
  }

  function layoutHeroMask() {
    var width = window.innerWidth;
    var height = window.innerHeight;
    var titleLength = Array.from(currentGroupName || COPY.TITLE_FALLBACK).length;
    var baseTitleSize = width * (width < 800 ? .09 : .058);
    var fittedTitleSize = width / Math.max(4.2, titleLength * .96);
    var titleSize = Math.min(baseTitleSize, fittedTitleSize);
    var subtitleSize = titleSize * .17;
    var centerX = width / 2;
    var centerY = height / 2;
    var titleY = centerY - titleSize * .08;
    var subtitleY = centerY + titleSize * .72;

    elements.heroCutout.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
    elements.heroMaskBase.setAttribute('width', width);
    elements.heroMaskBase.setAttribute('height', height);
    qsa('.hm-svg-title').forEach(function (text) {
      text.setAttribute('x', centerX);
      text.setAttribute('y', titleY);
      text.setAttribute('font-size', titleSize);
      text.setAttribute('dominant-baseline', 'middle');
    });
    qsa('.hm-svg-subtitle').forEach(function (text) {
      text.setAttribute('x', centerX);
      text.setAttribute('y', subtitleY);
      text.setAttribute('font-size', subtitleSize);
      text.setAttribute('dominant-baseline', 'middle');
    });
  }

  function setupReveal() {
    if (!('IntersectionObserver' in window)) {
      qsa('.reveal').forEach(function (element) { element.classList.add('visible'); });
      return;
    }
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add('visible');
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: .16, rootMargin: '0px 0px -6% 0px' });
    qsa('.reveal').forEach(function (element) { observer.observe(element); });
  }

  function updateHeaderCounterVisibility() {
    if (!elements.siteHeader || !elements.stories) return;
    var storiesTop = elements.stories.getBoundingClientRect().top;
    var storiesBottom = elements.stories.getBoundingClientRect().bottom;
    var marker = window.innerHeight * .45;
    var inStories = storiesTop <= marker && storiesBottom >= marker;
    var menuOpen = elements.menuPanel.classList.contains('open');
    elements.siteHeader.classList.toggle('header-counter-hidden', !inStories || menuOpen);

    if (!inStories) return;

    var closestIndex = 0;
    var closestDistance = Infinity;
    qsa('.photo-story').forEach(function (story, index) {
      var rect = story.getBoundingClientRect();
      var distance = marker >= rect.top && marker <= rect.bottom
        ? 0
        : Math.min(Math.abs(marker - rect.top), Math.abs(marker - rect.bottom));

      if (distance < closestDistance) {
        closestDistance = distance;
        closestIndex = index;
      }
    });

    elements.headerCurrent.textContent = String(closestIndex + 1).padStart(2, '0');
  }

  function preloadOpeningImages() {
    var sources = archivePhotos.slice(0, Math.min(5, archivePhotos.length)).map(function (photo) { return photo.path; });
    if (coverPhoto && coverPhoto.path) sources.unshift(coverPhoto.path);
    var seen = {};
    sources = sources.filter(function (p) { if (seen[p]) return false; seen[p] = true; return true; });
    var complete = 0;

    function done() {
      complete += 1;
      var percent = Math.round(complete / sources.length * 100);
      elements.loaderBar.style.width = percent + '%';
      elements.loaderValue.textContent = String(percent).padStart(2, '0');
      if (complete >= sources.length) {
        setTimeout(function () { elements.loader.classList.add('hidden'); }, 250);
      }
    }

    sources.forEach(function (source) {
      var image = new Image();
      image.onload = done;
      image.onerror = done;
      image.src = source;
      if (image.complete) {
        image.onload = null;
        image.onerror = null;
        done();
      }
    });
  }

  function toggleMenu() {
    var open = !elements.menuPanel.classList.contains('open');
    elements.menuPanel.classList.toggle('open', open);
    elements.menuButton.setAttribute('aria-expanded', String(open));
    elements.menuPanel.setAttribute('aria-hidden', String(!open));
    document.body.classList.toggle('menu-open', open);
    updateHeaderCounterVisibility();
  }

  function closeMenu() {
    elements.menuPanel.classList.remove('open');
    elements.menuPanel.classList.remove('switching');
    elements.menuButton.setAttribute('aria-expanded', 'false');
    elements.menuPanel.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('menu-open');
    updateHeaderCounterVisibility();
  }

  function openLightbox(index) {
    if (!archivePhotos[index]) return;
    lightboxIndex = index;
    updateLightbox();
    elements.lightbox.classList.add('active');
    elements.lightbox.setAttribute('aria-hidden', 'false');
    document.body.classList.add('lightbox-open');
  }

  function closeLightbox() {
    elements.lightbox.classList.remove('active');
    elements.lightbox.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('lightbox-open');
  }

  function navigateLightbox(direction) {
    lightboxIndex = (lightboxIndex + direction + archivePhotos.length) % archivePhotos.length;
    updateLightbox();
  }

  function updateLightbox() {
    var photo = archivePhotos[lightboxIndex];
    var image = qs('#lightbox-img');
    image.src = photo.path;
    image.alt = photo.location || photo.name;
    qs('#lightbox-caption').textContent = [photo.location, photo.date, photo.camera, photo.name].filter(Boolean).join(' · ');
    qs('#lightbox-counter').textContent = String(lightboxIndex + 1).padStart(2, '0') + ' / ' + String(archivePhotos.length).padStart(2, '0');
  }

  function setupDragScroll() {
    var viewport = elements.stripViewport;
    var dragging = false;
    var moved = false;
    var startX = 0;
    var startScroll = 0;
    var pressedPhotoIndex = null;

    viewport.addEventListener('pointerdown', function (event) {
      if (event.button !== 0) return;
      dragging = true;
      moved = false;
      startX = event.clientX;
      startScroll = viewport.scrollLeft;
      var photo = event.target.closest('[data-photo-index]');
      pressedPhotoIndex = photo ? Number(photo.dataset.photoIndex) : null;
      viewport.setPointerCapture(event.pointerId);
    });
    viewport.addEventListener('pointermove', function (event) {
      if (!dragging) return;
      if (Math.abs(event.clientX - startX) > 6) moved = true;
      if (!moved) return;
      viewport.scrollLeft = startScroll - (event.clientX - startX) * 1.5;
    });
    viewport.addEventListener('pointerup', function (event) {
      if (!dragging) return;
      dragging = false;
      if (viewport.hasPointerCapture(event.pointerId)) {
        viewport.releasePointerCapture(event.pointerId);
      }
      if (!moved && pressedPhotoIndex !== null) openLightbox(pressedPhotoIndex);
      pressedPhotoIndex = null;
    });
    viewport.addEventListener('pointercancel', function () {
      dragging = false;
      moved = false;
      pressedPhotoIndex = null;
    });
    viewport.addEventListener('click', function (event) {
      if (moved) {
        event.preventDefault();
        event.stopPropagation();
      }
      moved = false;
    }, true);
  }

  function setupLightboxSwipe() {
    var startX = 0;
    qs('#lightbox-img').addEventListener('touchstart', function (event) {
      startX = event.touches[0].clientX;
    }, { passive: true });
    qs('#lightbox-img').addEventListener('touchend', function (event) {
      var delta = event.changedTouches[0].clientX - startX;
      if (Math.abs(delta) > 50) navigateLightbox(delta > 0 ? -1 : 1);
    }, { passive: true });
  }

  function storyLine(index) {
    return COPY.QUOTES[index % COPY.QUOTES.length];
  }

  function cleanName(name) {
    return String(name || COPY.UNTITLED).replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ');
  }

  function splitLocation(location) {
    if (!location) return { title: '', detail: '' };
    var parts = String(location).split('·').map(function (part) { return part.trim(); }).filter(Boolean);
    var title = parts[0] || '';
    var detail = parts.slice(1).join(' · ');

    var bracket = title.match(/^(.+?)（(.+?)）$/);
    if (bracket) {
      title = bracket[1];
      detail = bracket[2] + (detail ? ' · ' + detail : '');
    }

    return { title: title, detail: detail };
  }

  function esc(value) {
    var div = document.createElement('div');
    div.textContent = value == null ? '' : String(value);
    return div.innerHTML;
  }

  function attr(value) {
    return esc(value).replace(/"/g, '&quot;');
  }

  function clamp(value, min, max) { return Math.min(max, Math.max(min, value)); }
  function mix(start, end, amount) { return start + (end - start) * amount; }
  function easeOutCubic(value) {
    return 1 - Math.pow(1 - value, 3);
  }
  function smoothstep(value) {
    return value * value * (3 - 2 * value);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
