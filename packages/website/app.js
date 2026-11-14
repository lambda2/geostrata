(function initWebsite() {
	var isFr = (document.documentElement.lang || 'en').toLowerCase().indexOf('fr') === 0
	var PAGE_SIZE = 24

	var state = {
		sources: [],
		filtered: [],
		category: 'ALL',
		country: 'ALL',
		protocolType: 'ALL',
		includeEli: true,
		family: '',
		query: '',
		visibleLimit: PAGE_SIZE
	}

	var lastOpenerId = null

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
		crisis: './icons/crisis.svg',
		population: './icons/population.svg',
		health: './icons/health.svg',
		hazard: './icons/hazard.svg',
		weather: './icons/weather.svg'
	}

	var CATEGORY_LABELS = isFr
		? {
				basemap: 'Fond de carte',
				imagery: 'Imagerie',
				elevation: 'Relief',
				hydrography: 'Hydrographie',
				admin: 'Limites administratives',
				buildings: 'Bâtiments',
				transport: 'Transport',
				utilities: 'Réseaux',
				landuse: 'Occupation du sol',
				crisis: 'Crise',
				population: 'Population',
				health: 'Santé',
				hazard: 'Aléas',
				weather: 'Météo'
			}
		: {
				basemap: 'Basemap',
				imagery: 'Imagery',
				elevation: 'Elevation',
				hydrography: 'Hydrography',
				admin: 'Admin boundaries',
				buildings: 'Buildings',
				transport: 'Transport',
				utilities: 'Utilities',
				landuse: 'Land use',
				crisis: 'Crisis',
				population: 'Population',
				health: 'Health',
				hazard: 'Hazards',
				weather: 'Weather'
			}

	var TEXT = isFr
		? {
				worldwide: 'Monde entier',
				countOne: ' couche',
				countMany: ' couches',
				familyCountries: ' pays',
				familyHint: 'Même couche publiée pays par pays. Ouvrez la famille ou choisissez une couverture pour voir un pays.',
				familyOpen: 'Voir les pays',
				countOf: ' sur ',
				emptyTitle: 'Aucune couche ne correspond',
				emptyHint: 'Essayez un autre mot-clé ou retirez un filtre.',
				reset: 'Réinitialiser les filtres',
				provider: 'Fournisseur',
				license: 'Licence',
				coverage: 'Couverture',
				theme: 'Thème',
				format: 'Format',
				dataDate: 'Date des données',
				linkStatus: 'État du lien',
				statusOk: 'Répond',
				statusDegraded: 'Lent ou instable',
				statusDead: 'Ne répond plus',
				checkedOn: 'vérifié le ',
				findInQgis: 'La trouver dans QGIS',
				serviceUrl: 'Adresse du service',
				overpassFilter: 'Filtre OpenStreetMap',
				fullDefinition: 'Définition complète (JSON)',
				copy: 'Copier',
				copied: 'Copié',
				loadError: 'Le catalogue n’a pas pu être chargé. Rechargez la page ou ouvrez catalog.json.',
				layers: 'couches'
			}
		: {
				worldwide: 'Worldwide',
				countOne: ' layer',
				countMany: ' layers',
				familyCountries: ' countries',
				familyHint: 'The same layer published country by country. Open the family or pick a coverage to see one country.',
				familyOpen: 'See countries',
				countOf: ' of ',
				emptyTitle: 'No layer matches',
				emptyHint: 'Try another keyword or remove a filter.',
				reset: 'Reset filters',
				provider: 'Provider',
				license: 'License',
				coverage: 'Coverage',
				theme: 'Theme',
				format: 'Format',
				dataDate: 'Data date',
				linkStatus: 'Link status',
				statusOk: 'Responding',
				statusDegraded: 'Slow or unstable',
				statusDead: 'Not responding',
				checkedOn: 'checked on ',
				findInQgis: 'Find it in QGIS',
				serviceUrl: 'Service URL',
				overpassFilter: 'OpenStreetMap filter',
				fullDefinition: 'Full definition (JSON)',
				copy: 'Copy',
				copied: 'Copied',
				loadError: 'The catalog could not be loaded. Reload the page or open catalog.json.',
				layers: 'layers'
			}

	var regionNames = null
	try {
		regionNames = new Intl.DisplayNames([isFr ? 'fr' : 'en'], { type: 'region' })
	} catch (e) {
		regionNames = null
	}

	function getCoverageLabel(countryIso) {
		if (!countryIso) {
			return TEXT.worldwide
		}
		try {
			var label = regionNames ? regionNames.of(countryIso) : null
			return label ? label : countryIso
		} catch (e) {
			return countryIso
		}
	}

	// Statuses come from the weekly endpoint check; a catalog built without it has no check date.
	function getLinkStatusLabel(item) {
		if (!item.health_checked_at) {
			return ''
		}
		var status = String(item.health_status || 'ok')
		var label = status === 'dead' ? TEXT.statusDead : status === 'degraded' ? TEXT.statusDegraded : TEXT.statusOk
		return label + ' (' + TEXT.checkedOn + String(item.health_checked_at).slice(0, 10) + ')'
	}

	var FAMILY_MIN_SIZE = 20
	var familyKeyById = {}
	var familyInfo = {}

	// A family is one layer published country by country (ids differ only by their country prefix).
	function buildFamilies(sources) {
		var members = {}
		for (var i = 0; i < sources.length; i++) {
			var item = sources[i]
			if (!item.country_iso) {
				continue
			}
			var key = String(item.id).replace(/^[a-z]{2}-/, '')
			if (!members[key]) {
				members[key] = []
			}
			members[key].push(item)
		}
		Object.keys(members).forEach(function registerFamily(key) {
			var list = members[key]
			if (list.length < FAMILY_MIN_SIZE) {
				return
			}
			// The family label is what all member names share before the country name.
			var prefix = getLocalizedName(list[0])
			for (var j = 1; j < list.length; j++) {
				var name = getLocalizedName(list[j])
				var k = 0
				while (k < prefix.length && k < name.length && prefix.charAt(k) === name.charAt(k)) {
					k++
				}
				prefix = prefix.slice(0, k)
			}
			var label = prefix.replace(/[\s\-–(:,]+$/, '')
			familyInfo[key] = { label: label ? label : key, count: list.length }
			for (var m = 0; m < list.length; m++) {
				familyKeyById[list[m].id] = key
			}
		})
	}

	// Families are folded into one card while browsing everything, and unfolded as soon as the visitor narrows down.
	function shouldFoldFamilies() {
		return state.country === 'ALL' && state.family === '' && state.query.trim() === ''
	}

	function buildDisplayList() {
		if (!shouldFoldFamilies()) {
			return state.filtered.map(function wrapItem(item) {
				return { item: item, family: null }
			})
		}
		var seen = {}
		var list = []
		for (var i = 0; i < state.filtered.length; i++) {
			var item = state.filtered[i]
			var key = familyKeyById[item.id]
			if (!key) {
				list.push({ item: item, family: null })
			} else if (!seen[key]) {
				seen[key] = true
				list.push({ item: item, family: key })
			}
		}
		return list
	}

	function isEliSource(item) {
		return String(item.id).indexOf('-eli-') !== -1 || String(item.id).indexOf('eli-') === 0
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
			.replace(/[̀-ͯ]/g, '')
			.toLowerCase()
	}

	function getLocalizedName(item) {
		if (isFr && item && item.i18n && item.i18n.fr && item.i18n.fr.name) {
			return String(item.i18n.fr.name)
		}
		return String(item && item.name ? item.name : '')
	}

	function getLocalizedDescription(item) {
		if (isFr && item && item.i18n && item.i18n.fr && item.i18n.fr.description) {
			return String(item.i18n.fr.description)
		}
		return String(item && item.description ? item.description : '')
	}

	function findSourceById(sourceId) {
		for (var i = 0; i < state.sources.length; i++) {
			if (state.sources[i].id === sourceId) {
				return state.sources[i]
			}
		}
		return null
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
			return getCoverageLabel(a).localeCompare(getCoverageLabel(b), isFr ? 'fr' : 'en')
		})
		for (var j = 0; j < codes.length; j++) {
			var c = codes[j]
			var opt = document.createElement('option')
			opt.value = c
			opt.textContent = getCoverageLabel(c === 'GLOBAL' ? null : c) + ' (' + counts[c] + ')'
			select.appendChild(opt)
		}
	}

	function renderPillCounts(sources) {
		var counts = {}
		for (var i = 0; i < sources.length; i++) {
			var key = String(sources[i].category || '').toUpperCase()
			counts[key] = (counts[key] ? counts[key] : 0) + 1
		}
		var pills = document.querySelectorAll('#category-pills .pill')
		for (var j = 0; j < pills.length; j++) {
			var cat = pills[j].getAttribute('data-category')
			var total = cat === 'ALL' ? sources.length : counts[cat] ? counts[cat] : 0
			var countEl = document.createElement('span')
			countEl.className = 'pill-count'
			countEl.textContent = String(total)
			pills[j].appendChild(countEl)
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
		if (filterValue === 'arcgis') {
			return t === 'arcgisfeatureserver' || t === 'arcgismapserver'
		}
		if (filterValue === 'cog') {
			return t === 'cog' || t === 'vrt'
		}
		if (filterValue === 'fgb') {
			return t === 'fgb' || t === 'geojson' || t === 'archive'
		}
		return t === filterValue.toLowerCase()
	}

	function hasActiveFilters() {
		return (
			state.query.trim() !== '' ||
			state.category !== 'ALL' ||
			state.country !== 'ALL' ||
			state.protocolType !== 'ALL' ||
			state.family !== '' ||
			!state.includeEli
		)
	}

	// Filters live in the query string so a search can be bookmarked or shared.
	function writeStateToUrl(openLayerId) {
		if (!window.history || !window.history.replaceState) {
			return
		}
		var params = new URLSearchParams()
		if (state.query.trim()) params.set('q', state.query.trim())
		if (state.category !== 'ALL') params.set('theme', state.category.toLowerCase())
		if (state.country !== 'ALL') params.set('coverage', state.country)
		if (state.protocolType !== 'ALL') params.set('format', state.protocolType)
		if (state.family) params.set('family', state.family)
		if (!state.includeEli) params.set('eli', '0')
		if (openLayerId) params.set('layer', openLayerId)
		var qs = params.toString()
		try {
			window.history.replaceState(null, '', window.location.pathname + (qs ? '?' + qs : '') + window.location.hash)
		} catch (e) {
			// file:// previews refuse replaceState, the explorer still works without it.
		}
	}

	function readStateFromUrl() {
		var params = new URLSearchParams(window.location.search)
		state.query = params.get('q') || ''
		state.category = (params.get('theme') || 'ALL').toUpperCase()
		state.country = params.get('coverage') || 'ALL'
		state.protocolType = params.get('format') || 'ALL'
		state.includeEli = params.get('eli') !== '0'
		state.family = familyInfo[params.get('family') || ''] ? params.get('family') : ''
		return params.get('layer')
	}

	function syncControlsFromState() {
		var searchInput = document.getElementById('catalog-search-input')
		var countrySelect = document.getElementById('country-select')
		var typeSelect = document.getElementById('type-select')
		var eliCheckbox = document.getElementById('include-eli-checkbox')
		if (searchInput) searchInput.value = state.query
		if (countrySelect) {
			countrySelect.value = state.country
			if (countrySelect.value !== state.country) {
				state.country = 'ALL'
				countrySelect.value = 'ALL'
			}
		}
		if (typeSelect) {
			typeSelect.value = state.protocolType
			if (typeSelect.value !== state.protocolType) {
				state.protocolType = 'ALL'
				typeSelect.value = 'ALL'
			}
		}
		if (eliCheckbox) eliCheckbox.checked = state.includeEli

		var pills = document.querySelectorAll('#category-pills .pill')
		var matched = false
		for (var i = 0; i < pills.length; i++) {
			var isActive = pills[i].getAttribute('data-category') === state.category
			matched = matched || isActive
			pills[i].classList.toggle('active', isActive)
			pills[i].setAttribute('aria-pressed', isActive ? 'true' : 'false')
		}
		if (!matched && pills.length > 0) {
			state.category = 'ALL'
			pills[0].classList.add('active')
			pills[0].setAttribute('aria-pressed', 'true')
		}
	}

	function resetFilters() {
		state.query = ''
		state.category = 'ALL'
		state.country = 'ALL'
		state.protocolType = 'ALL'
		state.family = ''
		state.includeEli = true
		state.visibleLimit = PAGE_SIZE
		syncControlsFromState()
		applyFilters()
	}

	function applyFilters() {
		var q = normalizeText(state.query).trim()
		var tokens = q ? q.split(/\s+/) : []

		state.filtered = state.sources.filter(function filterItem(item) {
			if (!state.includeEli && isEliSource(item)) {
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
			if (state.family !== '' && familyKeyById[item.id] !== state.family) {
				return false
			}
			if (tokens.length > 0) {
				var i18nFrName = item.i18n && item.i18n.fr && item.i18n.fr.name ? item.i18n.fr.name : ''
				var i18nFrDesc = item.i18n && item.i18n.fr && item.i18n.fr.description ? item.i18n.fr.description : ''
				var haystack = normalizeText(
					[
						item.id,
						item.name,
						item.description,
						i18nFrName,
						i18nFrDesc,
						item.attribution,
						item.category,
						item.country_iso,
						getCoverageLabel(item.country_iso),
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

		writeStateToUrl(null)
		renderGrid()
	}

	function renderGrid() {
		var grid = document.getElementById('catalog-grid')
		var countEl = document.getElementById('catalog-results-count')
		var loadMoreWrap = document.getElementById('load-more-wrap')
		var resetBtn = document.getElementById('reset-filters-btn')

		if (!grid) {
			return
		}

		if (countEl) {
			var n = state.filtered.length
			countEl.textContent =
				n === state.sources.length
					? n + TEXT.countMany
					: n + (n === 1 ? TEXT.countOne : TEXT.countMany) + TEXT.countOf + state.sources.length
		}
		if (resetBtn) {
			resetBtn.hidden = !hasActiveFilters()
		}

		var displayList = buildDisplayList()
		var slice = displayList.slice(0, state.visibleLimit)
		if (slice.length === 0) {
			grid.innerHTML =
				'<li class="catalog-empty">' +
				'<h3>' +
				escapeHtml(TEXT.emptyTitle) +
				'</h3>' +
				'<p>' +
				escapeHtml(TEXT.emptyHint) +
				'</p>' +
				'<button type="button" class="btn btn-secondary" data-reset-filters>' +
				escapeHtml(TEXT.reset) +
				'</button>' +
				'</li>'
			if (loadMoreWrap) {
				loadMoreWrap.hidden = true
			}
			return
		}

		var html = []
		for (var i = 0; i < slice.length; i++) {
			var item = slice[i].item
			var family = slice[i].family ? familyInfo[slice[i].family] : null
			var catKey = String(item.category || '').toLowerCase()
			var categoryLabel = CATEGORY_LABELS[catKey] ? CATEGORY_LABELS[catKey] : item.category
			var description = getLocalizedDescription(item)

			html.push(
				'<li>' +
					'<article class="source-card">' +
					'<div class="source-card-top">' +
					'<img class="source-card-icon" src="' +
					escapeHtml(getIconForSource(item)) +
					'" alt="" />' +
					'<div>' +
					'<h3 class="source-card-title">' +
					(family
						? '<button type="button" class="source-card-open" data-open-family="' +
							escapeHtml(slice[i].family) +
							'">' +
							escapeHtml(family.label) +
							'</button>'
						: '<button type="button" class="source-card-open" data-open-id="' +
							escapeHtml(item.id) +
							'">' +
							escapeHtml(getLocalizedName(item)) +
							'</button>') +
					'</h3>' +
					'<div class="source-card-provider">' +
					escapeHtml(categoryLabel) +
					'</div>' +
					'</div>' +
					'</div>' +
					(family
						? '<p class="source-card-desc">' + escapeHtml(TEXT.familyHint) + '</p>'
						: description
							? '<p class="source-card-desc">' + escapeHtml(description) + '</p>'
							: '') +
					'<div class="source-badges">' +
					'<span class="badge badge-country">' +
					escapeHtml(family ? family.count + TEXT.familyCountries : getCoverageLabel(item.country_iso)) +
					'</span>' +
					'<span class="badge badge-type">' +
					escapeHtml(String(item.type || '').toUpperCase()) +
					'</span>' +
					(isEliSource(item) ? '<span class="badge badge-eli">OSM ELI</span>' : '') +
					(item.health_status === 'dead' ? '<span class="badge badge-dead">' + escapeHtml(TEXT.statusDead) + '</span>' : '') +
					'<span class="badge">' +
					escapeHtml(item.license) +
					'</span>' +
					'</div>' +
					'</article>' +
					'</li>'
			)
		}

		grid.innerHTML = html.join('')

		if (loadMoreWrap) {
			loadMoreWrap.hidden = displayList.length <= state.visibleLimit
		}
	}

	function copyBlockHtml(value, elementId) {
		return (
			'<div class="copy-box">' +
			'<code id="' +
			elementId +
			'">' +
			escapeHtml(value) +
			'</code>' +
			'<button type="button" class="btn btn-secondary btn-sm" data-copy-target="' +
			elementId +
			'">' +
			escapeHtml(TEXT.copy) +
			'</button>' +
			'</div>'
		)
	}

	function openSourceModal(sourceId) {
		var item = findSourceById(sourceId)
		var modal = document.getElementById('source-modal')
		var titleEl = document.getElementById('modal-title')
		var idEl = document.getElementById('modal-id')
		var iconEl = document.getElementById('modal-icon')
		var bodyEl = document.getElementById('modal-body')

		if (!item || !modal || !titleEl || !idEl || !iconEl || !bodyEl) {
			return
		}

		var localizedTitle = getLocalizedName(item)
		var localizedDesc = getLocalizedDescription(item)
		var catKey = String(item.category || '').toLowerCase()
		var isOverpass = String(item.type || '').toLowerCase() === 'overpass'
		var linkStatus = getLinkStatusLabel(item)
		titleEl.textContent = localizedTitle
		idEl.textContent = item.id
		iconEl.src = getIconForSource(item)

		var firstKeyword = Array.isArray(item.tags) && item.tags.length > 0 ? item.tags[0] : localizedTitle.split(/\s+/)[0]
		bodyEl.innerHTML =
			(localizedDesc ? '<p>' + escapeHtml(localizedDesc) + '</p>' : '') +
			'<dl class="modal-facts">' +
			'<dt>' +
			escapeHtml(TEXT.provider) +
			'</dt><dd>' +
			escapeHtml(item.attribution) +
			'</dd>' +
			'<dt>' +
			escapeHtml(TEXT.license) +
			'</dt><dd>' +
			escapeHtml(item.license) +
			'</dd>' +
			'<dt>' +
			escapeHtml(TEXT.coverage) +
			'</dt><dd>' +
			escapeHtml(getCoverageLabel(item.country_iso)) +
			'</dd>' +
			'<dt>' +
			escapeHtml(TEXT.theme) +
			'</dt><dd>' +
			escapeHtml(CATEGORY_LABELS[catKey] ? CATEGORY_LABELS[catKey] : item.category) +
			'</dd>' +
			'<dt>' +
			escapeHtml(TEXT.format) +
			'</dt><dd>' +
			escapeHtml(String(item.type || '').toUpperCase()) +
			(isOverpass ? '' : ', ' + escapeHtml(item.crs)) +
			'</dd>' +
			(item.data_date ? '<dt>' + escapeHtml(TEXT.dataDate) + '</dt><dd>' + escapeHtml(item.data_date) + '</dd>' : '') +
			(linkStatus ? '<dt>' + escapeHtml(TEXT.linkStatus) + '</dt><dd>' + escapeHtml(linkStatus) + '</dd>' : '') +
			'</dl>' +
			'<h3>' +
			escapeHtml(TEXT.findInQgis) +
			' (<kbd>Ctrl</kbd>+<kbd>K</kbd>)</h3>' +
			copyBlockHtml('gst ' + String(firstKeyword).toLowerCase(), 'modal-qgis-query') +
			'<h3>' +
			escapeHtml(isOverpass ? TEXT.overpassFilter : TEXT.serviceUrl) +
			'</h3>' +
			copyBlockHtml(item.url, 'modal-service-url') +
			'<details><summary>' +
			escapeHtml(TEXT.fullDefinition) +
			'</summary>' +
			'<pre class="code-block"><code>' +
			escapeHtml(JSON.stringify(item, null, 2)) +
			'</code></pre></details>'

		lastOpenerId = sourceId
		writeStateToUrl(sourceId)
		if (typeof modal.showModal === 'function') {
			if (!modal.open) {
				modal.showModal()
			}
		} else {
			modal.setAttribute('open', '')
		}
	}

	function copyFromTarget(btn) {
		var target = document.getElementById(btn.getAttribute('data-copy-target') || '')
		if (!target || !navigator.clipboard) {
			return
		}
		navigator.clipboard.writeText(target.textContent || '').then(function onCopied() {
			btn.textContent = TEXT.copied
			setTimeout(function resetCopyText() {
				btn.textContent = TEXT.copy
			}, 2000)
		})
	}

	function bindEvents() {
		var form = document.getElementById('catalog-form')
		if (form) {
			form.addEventListener('submit', function onSubmit(e) {
				e.preventDefault()
			})
		}

		var searchInput = document.getElementById('catalog-search-input')
		if (searchInput) {
			searchInput.addEventListener('input', function onSearchInput(e) {
				state.query = e.target.value
				state.visibleLimit = PAGE_SIZE
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
				state.category = btn.getAttribute('data-category') || 'ALL'
				state.visibleLimit = PAGE_SIZE
				syncControlsFromState()
				applyFilters()
			})
		}

		var countrySelect = document.getElementById('country-select')
		if (countrySelect) {
			countrySelect.addEventListener('change', function onCountryChange(e) {
				state.country = e.target.value
				state.visibleLimit = PAGE_SIZE
				applyFilters()
			})
		}

		var typeSelect = document.getElementById('type-select')
		if (typeSelect) {
			typeSelect.addEventListener('change', function onTypeChange(e) {
				state.protocolType = e.target.value
				state.visibleLimit = PAGE_SIZE
				applyFilters()
			})
		}

		var eliCheckbox = document.getElementById('include-eli-checkbox')
		if (eliCheckbox) {
			eliCheckbox.addEventListener('change', function onEliChange(e) {
				state.includeEli = Boolean(e.target.checked)
				state.visibleLimit = PAGE_SIZE
				applyFilters()
			})
		}

		var resetBtn = document.getElementById('reset-filters-btn')
		if (resetBtn) {
			resetBtn.addEventListener('click', resetFilters)
		}

		var loadMoreBtn = document.getElementById('load-more-btn')
		if (loadMoreBtn) {
			loadMoreBtn.addEventListener('click', function onLoadMore() {
				var firstNewIndex = state.visibleLimit
				state.visibleLimit += PAGE_SIZE
				renderGrid()
				// Keep keyboard users where they were instead of dropping focus on the body.
				var openers = document.querySelectorAll('#catalog-grid .source-card-open')
				if (openers[firstNewIndex]) {
					openers[firstNewIndex].focus()
				}
			})
		}

		var grid = document.getElementById('catalog-grid')
		if (grid) {
			grid.addEventListener('click', function onGridClick(e) {
				if (e.target.closest('[data-reset-filters]')) {
					resetFilters()
					return
				}
				var familyBtn = e.target.closest('[data-open-family]')
				if (familyBtn) {
					state.family = familyBtn.getAttribute('data-open-family') || ''
					state.visibleLimit = PAGE_SIZE
					applyFilters()
					var firstOpener = document.querySelector('#catalog-grid [data-open-id]')
					if (firstOpener) {
						firstOpener.focus()
					}
					return
				}
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
				modal.close()
			})
			modal.addEventListener('click', function onBackdropClick(e) {
				if (e.target === modal) {
					modal.close()
				}
			})
			modal.addEventListener('close', function onModalClosed() {
				writeStateToUrl(null)
				var opener = lastOpenerId
					? document.querySelector('#catalog-grid [data-open-id="' + lastOpenerId + '"]')
					: null
				if (opener) {
					opener.focus()
				}
			})
		}

		document.addEventListener('click', function onCopyClick(e) {
			var btn = e.target.closest('[data-copy-target]')
			if (btn) {
				copyFromTarget(btn)
			}
		})
	}

	function loadCatalogData() {
		Promise.all([
			fetch('./catalog.json').then(function parseCatalog(r) {
				if (!r.ok) {
					throw new Error('catalog.json: HTTP ' + r.status)
				}
				return r.json()
			}),
			fetch('./manifest.json')
				.then(function parseManifest(r) {
					return r.ok ? r.json() : null
				})
				.catch(function onManifestError() {
					return null
				})
		])
			.then(function onLoaded(results) {
				var sources = Array.isArray(results[0]) ? results[0] : []
				var manifest = results[1]

				state.sources = sources
				var heroCount = document.getElementById('hero-total-count')
				var versionBadge = document.getElementById('catalog-version-badge')

				if (heroCount) {
					heroCount.textContent = String(sources.length)
				}
				if (versionBadge) {
					versionBadge.textContent = manifest
						? 'v' + manifest.version + ', ' + sources.length + ' ' + TEXT.layers + ', sha256 ' + String(manifest.sha256).slice(0, 10)
						: sources.length + ' ' + TEXT.layers
				}

				buildFamilies(sources)
				populateCountrySelect(sources)
				renderPillCounts(sources)
				var layerToOpen = readStateFromUrl()
				syncControlsFromState()
				applyFilters()
				if (layerToOpen) {
					openSourceModal(layerToOpen)
				}
			})
			.catch(function onLoadError() {
				var countEl = document.getElementById('catalog-results-count')
				if (countEl) {
					countEl.textContent = TEXT.loadError
				}
			})
	}

	updateRepoXmlUrl()
	bindEvents()
	loadCatalogData()
})()
