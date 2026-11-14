(function initWebsite() {
	var isFr = (document.documentElement.lang || 'en').toLowerCase().indexOf('fr') === 0

	var state = {
		sources: [],
		filtered: [],
		category: 'ALL',
		country: 'ALL',
		protocolType: 'ALL',
		includeEli: true,
		query: '',
		visibleLimit: 24
	}

	var CATEGORY_ICONS = {
		basemap: './icons/basemap.svg',
		imagery: './icons/imagery.svg',
		elevation: './icons/elevation.svg',
		hydrography: './icons/hydrography.svg',
		admin: './icons/admin.svg',
		buildings: './icons/buildings.svg',
		transport: './icons/transport.svg',
		utilities: './icons/utilities.svg',
		landuse: './icons/landuse.svg',
		crisis: './icons/crisis.svg'
	}

	var CATEGORY_LABELS = isFr
		? {
				basemap: 'Fond de carte',
				imagery: 'Imagerie',
				elevation: 'Élévation & MNT',
				hydrography: 'Hydrographie',
				admin: 'Administratif',
				buildings: 'Bâtiments',
				transport: 'Transport',
				utilities: 'Réseaux',
				landuse: 'Occupation du sol',
				crisis: 'Crise & Risques'
			}
		: {
				basemap: 'Basemap',
				imagery: 'Imagery',
				elevation: 'Elevation & DEM',
				hydrography: 'Hydrography',
				admin: 'Admin Boundaries',
				buildings: 'Buildings',
				transport: 'Transport',
				utilities: 'Utilities',
				landuse: 'Landuse',
				crisis: 'Crisis & Relief'
			}

	function getIconForSource(source) {
		var srcType = String(source.type || '').toLowerCase()
		var srcCat = String(source.category || '').toLowerCase()
		if (srcType === 'overpass') {
			return './icons/overpass.svg'
		}
		return CATEGORY_ICONS[srcCat] ? CATEGORY_ICONS[srcCat] : './icons/geostrata.svg'
	}

	function escapeHtml(str) {
		return String(str ? str : '')
			.replace(/&/g, '&amp;')
			.replace(/</g, '&lt;')
			.replace(/>/g, '&gt;')
			.replace(/"/g, '&quot;')
	}

	function normalizeText(str) {
		return String(str ? str : '')
			.normalize('NFD')
			.replace(/[\u0300-\u036f]/g, '')
			.toLowerCase()
	}

	function updateRepoXmlUrl() {
		var el = document.getElementById('repo-xml-url')
		if (!el) {
			return
		}
		if (window.location.protocol.indexOf('http') === 0) {
			var base = window.location.origin + window.location.pathname.replace(/\/index(\.fr)?\.html$/, '').replace(/\/$/, '')
			el.textContent = base + '/plugins.xml'
		}
	}

	function populateCountrySelect(sources) {
		var select = document.getElementById('country-select')
		if (!select) {
			return
		}
		var counts = {}
		for (var i = 0; i < sources.length; i++) {
			var code = sources[i].country_iso ? sources[i].country_iso : 'GLOBAL'
			counts[code] = (counts[code] ? counts[code] : 0) + 1
		}
		var codes = Object.keys(counts).sort(function compareCodes(a, b) {
			if (a === 'GLOBAL') return -1
			if (b === 'GLOBAL') return 1
			return counts[b] - counts[a]
		})
		for (var j = 0; j < codes.length; j++) {
			var c = codes[j]
			var opt = document.createElement('option')
			opt.value = c
			var globalText = isFr ? '🌐 Monde entier (Global)' : '🌐 Worldwide (Global)'
			opt.textContent = (c === 'GLOBAL' ? globalText : '🏳️ ' + c) + ' (' + counts[c] + ')'
			select.appendChild(opt)
		}
	}

	function matchesProtocolFilter(itemType, filterValue) {
		if (filterValue === 'ALL') {
			return true
		}
		var t = String(itemType || '').toLowerCase()
		if (filterValue === 'xyz') {
			return t === 'xyz' || t === 'tms'
		}
		if (filterValue === 'cog') {
			return t === 'cog' || t === 'vrt'
		}
		if (filterValue === 'fgb') {
			return t === 'fgb' || t === 'geojson' || t === 'archive'
		}
		return t === filterValue.toLowerCase()
	}

	function applyFilters() {
		var q = normalizeText(state.query).trim()
		var tokens = q ? q.split(/\s+/) : []

		state.filtered = state.sources.filter(function filterItem(item) {
			var isEli = String(item.id).indexOf('-eli-') !== -1 || String(item.id).indexOf('eli-') === 0
			if (!state.includeEli && isEli) {
				return false
			}
			if (state.category !== 'ALL' && String(item.category).toUpperCase() !== state.category) {
				return false
			}
			if (state.country !== 'ALL') {
				var itemCountry = item.country_iso ? item.country_iso : 'GLOBAL'
				if (itemCountry !== state.country) {
					return false
				}
			}
			if (!matchesProtocolFilter(item.type, state.protocolType)) {
				return false
			}
			if (tokens.length > 0) {
				var haystack = normalizeText(
					[
						item.id,
						item.name,
						item.description,
						item.attribution,
						item.category,
						item.country_iso,
						Array.isArray(item.tags) ? item.tags.join(' ') : ''
					].join(' ')
				)
				for (var t = 0; t < tokens.length; t++) {
					if (haystack.indexOf(tokens[t]) === -1) {
						return false
					}
				}
			}
			return true
		})

		renderGrid()
	}

	function renderGrid() {
		var grid = document.getElementById('catalog-grid')
		var countEl = document.getElementById('catalog-results-count')
		var loadMoreWrap = document.getElementById('load-more-wrap')

		if (!grid) {
			return
		}

		if (countEl) {
			if (isFr) {
				countEl.textContent =
					state.filtered.length +
					' couche' +
					(state.filtered.length > 1 ? 's' : '') +
					' affichée' +
					(state.filtered.length > 1 ? 's' : '') +
					' sur ' +
					state.sources.length +
					' au total'
			} else {
				countEl.textContent =
					'Showing ' +
					state.filtered.length +
					' matching layer' +
					(state.filtered.length === 1 ? '' : 's') +
					' out of ' +
					state.sources.length +
					' total'
			}
		}

		var slice = state.filtered.slice(0, state.visibleLimit)
		if (slice.length === 0) {
			grid.innerHTML =
				'<div class="feature-card" style="grid-column: 1 / -1; text-align: center;">' +
				'<h3>' +
				(isFr ? 'Aucune couche ne correspond à ces critères' : 'No layers match your search criteria') +
				'</h3>' +
				'<p>' +
				(isFr
					? 'Essayez un autre mot-clé ou réinitialisez les filtres de catégorie/pays.'
					: 'Try another keyword or reset the category/country filters.') +
				'</p>' +
				'</div>'
			if (loadMoreWrap) {
				loadMoreWrap.hidden = true
			}
			return
		}

		var detailsLabel = isFr ? 'Détails &amp; URI' : 'Details &amp; URI'
		var html = []
		for (var i = 0; i < slice.length; i++) {
			var item = slice[i]
			var isEli = String(item.id).indexOf('-eli-') !== -1 || String(item.id).indexOf('eli-') === 0
			var countryLabel = item.country_iso ? item.country_iso : 'GLOBAL'
			var catKey = String(item.category || '').toLowerCase()
			var categoryLabel = CATEGORY_LABELS[catKey] ? CATEGORY_LABELS[catKey] : item.category
			var tags = Array.isArray(item.tags) ? item.tags.slice(0, 4) : []

			var tagsHtml = tags
				.map(function mapTag(tag) {
					return '<span class="badge">#' + escapeHtml(tag) + '</span>'
				})
				.join('')

			html.push(
				'<article class="source-card">' +
					'<div>' +
					'<div class="source-card-top">' +
					'<img class="source-card-icon" src="' +
					escapeHtml(getIconForSource(item)) +
					'" alt="" />' +
					'<div>' +
					'<h3 class="source-card-title">' +
					escapeHtml(item.name) +
					'</h3>' +
					'<div class="source-card-provider">' +
					escapeHtml(item.attribution) +
					' &bull; ' +
					escapeHtml(categoryLabel) +
					'</div>' +
					'</div>' +
					'</div>' +
					'<div class="source-badges" style="margin-top: 12px;">' +
					'<span class="badge badge-type">' +
					escapeHtml(String(item.type || '').toUpperCase()) +
					'</span>' +
					'<span class="badge badge-country">' +
					escapeHtml(countryLabel) +
					'</span>' +
					(isEli ? '<span class="badge badge-eli">OSM ELI</span>' : '') +
					tagsHtml +
					'</div>' +
					'</div>' +
					'<div class="source-card-footer">' +
					'<span>' +
					escapeHtml(item.license) +
					' (' +
					escapeHtml(item.crs) +
					')</span>' +
					'<button type="button" class="btn btn-sm btn-outline" data-open-id="' +
					escapeHtml(item.id) +
					'">' +
					detailsLabel +
					'</button>' +
					'</div>' +
					'</article>'
			)
		}

		grid.innerHTML = html.join('')

		if (loadMoreWrap) {
			loadMoreWrap.hidden = state.filtered.length <= state.visibleLimit
		}
	}

	function openSourceModal(sourceId) {
		var item = null
		for (var i = 0; i < state.sources.length; i++) {
			if (state.sources[i].id === sourceId) {
				item = state.sources[i]
				break
			}
		}
		if (!item) {
			return
		}
		var modal = document.getElementById('source-modal')
		var titleEl = document.getElementById('modal-title')
		var idEl = document.getElementById('modal-id')
		var iconEl = document.getElementById('modal-icon')
		var bodyEl = document.getElementById('modal-body')

		if (!modal || !titleEl || !idEl || !iconEl || !bodyEl) {
			return
		}

		titleEl.textContent = item.name
		idEl.textContent = item.id
		iconEl.src = getIconForSource(item)

		var firstKeyword = Array.isArray(item.tags) && item.tags.length > 0 ? item.tags[0] : item.name.split(/\s+/)[0]
		bodyEl.innerHTML =
			'<p>' +
			escapeHtml(item.description) +
			'</p>' +
			'<p><strong>' +
			(isFr ? 'Attribution :' : 'Attribution:') +
			'</strong> ' +
			escapeHtml(item.attribution) +
			' &nbsp;|&nbsp; <strong>' +
			(isFr ? 'Licence :' : 'License:') +
			'</strong> ' +
			escapeHtml(item.license) +
			' &nbsp;|&nbsp; <strong>CRS:</strong> <code>' +
			escapeHtml(item.crs) +
			'</code></p>' +
			'<p><strong>' +
			(isFr ? 'Commande rapide dans QGIS (<kbd>Ctrl</kbd>+<kbd>K</kbd>) :' : 'Quick command in QGIS (<kbd>Ctrl</kbd>+<kbd>K</kbd>):') +
			'</strong></p>' +
			'<pre class="code-block"><code>gst ' +
			escapeHtml(String(firstKeyword).toLowerCase()) +
			'</code></pre>' +
			'<p><strong>' +
			(isFr ? 'Définition complète (JSON / YAML compilé) :' : 'Full compiled definition (JSON / YAML):') +
			'</strong></p>' +
			'<pre class="code-block"><code>' +
			escapeHtml(JSON.stringify(item, null, 2)) +
			'</code></pre>'

		modal.hidden = false
	}

	function bindEvents() {
		var searchInput = document.getElementById('catalog-search-input')
		if (searchInput) {
			searchInput.addEventListener('input', function onSearchInput(e) {
				state.query = e.target.value
				state.visibleLimit = 24
				applyFilters()
			})
		}

		var pillsContainer = document.getElementById('category-pills')
		if (pillsContainer) {
			pillsContainer.addEventListener('click', function onPillClick(e) {
				var btn = e.target.closest('.pill')
				if (!btn) {
					return
				}
				var allPills = pillsContainer.querySelectorAll('.pill')
				for (var i = 0; i < allPills.length; i++) {
					allPills[i].classList.remove('active')
				}
				btn.classList.add('active')
				state.category = btn.getAttribute('data-category') || 'ALL'
				state.visibleLimit = 24
				applyFilters()
			})
		}

		var countrySelect = document.getElementById('country-select')
		if (countrySelect) {
			countrySelect.addEventListener('change', function onCountryChange(e) {
				state.country = e.target.value
				state.visibleLimit = 24
				applyFilters()
			})
		}

		var typeSelect = document.getElementById('type-select')
		if (typeSelect) {
			typeSelect.addEventListener('change', function onTypeChange(e) {
				state.protocolType = e.target.value
				state.visibleLimit = 24
				applyFilters()
			})
		}

		var eliCheckbox = document.getElementById('include-eli-checkbox')
		if (eliCheckbox) {
			eliCheckbox.addEventListener('change', function onEliChange(e) {
				state.includeEli = Boolean(e.target.checked)
				state.visibleLimit = 24
				applyFilters()
			})
		}

		var loadMoreBtn = document.getElementById('load-more-btn')
		if (loadMoreBtn) {
			loadMoreBtn.addEventListener('click', function onLoadMore() {
				state.visibleLimit += 24
				renderGrid()
			})
		}

		var grid = document.getElementById('catalog-grid')
		if (grid) {
			grid.addEventListener('click', function onGridClick(e) {
				var btn = e.target.closest('[data-open-id]')
				if (btn) {
					openSourceModal(btn.getAttribute('data-open-id'))
				}
			})
		}

		var modal = document.getElementById('source-modal')
		var closeBtn = document.getElementById('modal-close-btn')
		if (closeBtn && modal) {
			closeBtn.addEventListener('click', function onCloseModal() {
				modal.hidden = true
			})
			modal.addEventListener('click', function onBackdropClick(e) {
				if (e.target === modal) {
					modal.hidden = true
				}
			})
		}

		var copyBtn = document.getElementById('copy-repo-url-btn')
		if (copyBtn) {
			copyBtn.addEventListener('click', function onCopyUrl() {
				var urlEl = document.getElementById('repo-xml-url')
				if (urlEl && navigator.clipboard) {
					navigator.clipboard.writeText(urlEl.textContent || '')
					copyBtn.textContent = isFr ? 'Copié ✓' : 'Copied ✓'
					setTimeout(function resetCopyText() {
						copyBtn.textContent = isFr ? 'Copier' : 'Copy'
					}, 2000)
				}
			})
		}
	}

	function loadCatalogData() {
		Promise.all([
			fetch('./catalog.json').then(function parseCatalog(r) {
				return r.ok ? r.json() : []
			}),
			fetch('./manifest.json').then(function parseManifest(r) {
				return r.ok ? r.json() : null
			})
		])
			.then(function onLoaded(results) {
				var sources = Array.isArray(results[0]) ? results[0] : []
				var manifest = results[1]

				state.sources = sources
				var heroCount = document.getElementById('hero-total-count')
				var statCount = document.getElementById('stat-sources-count')
				var versionBadge = document.getElementById('catalog-version-badge')

				if (heroCount) {
					heroCount.textContent = String(sources.length)
				}
				if (statCount) {
					statCount.textContent = String(sources.length)
				}
				if (versionBadge && manifest) {
					var totalCount = manifest.source_count ? manifest.source_count : sources.length
					versionBadge.textContent =
						'v' + manifest.version + ' (' + totalCount + ' sources • SHA-256: ' + String(manifest.sha256).slice(0, 10) + '…)'
				}

				populateCountrySelect(sources)
				applyFilters()
			})
			.catch(function onLoadError() {
				var countEl = document.getElementById('catalog-results-count')
				if (countEl) {
					countEl.textContent = isFr
						? 'Exécutez npm run build:site pour générer catalog.json.'
						: 'Run npm run build:site to generate catalog.json.'
				}
			})
	}

	updateRepoXmlUrl()
	bindEvents()
	loadCatalogData()
})()
