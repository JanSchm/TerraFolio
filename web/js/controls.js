/**
 * controls.js — the Alpine.data() factories behind the control library.
 *
 * Every control in this file is a thin layer over a real <input type="range">,
 * <input type="number">, <input type="radio"> or <button>. That is the whole
 * design: the mockup hand-rolled substitutes out of divs and lost keyboard
 * operation, focus, form semantics and screen-reader support along with them.
 * Keeping the native element means §12's accessibility requirements mostly
 * arrive for free, and the factory is left with the genuinely presentational
 * parts — a formatted readout, the split bar's geometry, an aria state.
 *
 * Two rules the factories enforce so the markup cannot forget them:
 *
 *   - Every numeric readout goes through format.js. A range's aria-valuetext is
 *     set from the same string a sighted user reads, so "€1,200m" is announced
 *     rather than "1200".
 *   - No state is carried by colour alone (epic §5). Toggles expose aria-checked,
 *     chips expose aria-pressed, and a KPI tile's compliance state comes with a
 *     mark and a spoken label as well as a colour.
 *
 * Controls do not know about the mandate, the pipeline or the API. Each one
 * dispatches a bubbling `tf:change` CustomEvent carrying { name, value }, which
 * is the seam issue #11 wires data onto.
 *
 * Classic script, not an ES module: the pages have to open over file://.
 * See docs/decisions.md D1.
 */
(function (root) {
  'use strict';

  /* Same dual resolution as feasibility.js: the browser loads format.js as a classic
     script before this one, while node requires it. Without the node branch the
     factories cannot be unit-tested at all. */
  var fmt = (typeof require === 'function')
    ? require('./format.js')
    : root.TerraFolio && root.TerraFolio.format;
  if (!fmt) throw new Error('controls.js requires format.js to be loaded first');

  var uid = 0;
  function nextId(prefix) {
    uid += 1;
    return prefix + '-' + uid;
  }

  /** Announces a control's new value to whatever is listening above it. */
  function emit(el, name, value) {
    if (!el || !el.dispatchEvent) return;
    el.dispatchEvent(new CustomEvent('tf:change', {
      detail: { name: name, value: value },
      bubbles: true,
    }));
  }

  /**
   * A slider with its value shown in the condensed face.
   *
   * `format` names a function on format.js, so a readout can never drift from
   * the rest of the product. `scale` divides the raw input value before
   * formatting, for the controls whose native range works in whole percent but
   * whose value is a fraction.
   */
  function rangeField(options) {
    var o = options || {};
    return {
      id: nextId('range'),
      name: o.name || '',
      value: o.value,
      min: o.min,
      max: o.max,
      step: o.step,
      scale: o.scale || 1,
      formatter: o.format || 'count',
      get display() {
        return fmt[this.formatter](this.value / this.scale);
      },
      changed: function ($event) {
        emit($event.target, this.name, this.value / this.scale);
      },
    };
  }

  /**
   * A spinner. Same contract as rangeField, without the large readout.
   *
   * `scale` divides the displayed value before it is emitted, for the fields the
   * user types in whole percent but the mandate stores as a fraction. Without it
   * a "max 35% merchant" cap leaves here as 35 and reaches the objective as
   * 3500% — see docs/decisions.md D13, which requires fractions throughout.
   */
  function numberField(options) {
    var o = options || {};
    return {
      id: nextId('number'),
      name: o.name || '',
      value: o.value,
      min: o.min,
      max: o.max,
      step: o.step,
      scale: o.scale || 1,
      changed: function ($event) {
        emit($event.target, this.name, this.value / this.scale);
      },
    };
  }

  /**
   * A set of toggle buttons — eligible countries, stages in scope.
   *
   * Each chip is a <button aria-pressed>, so the pressed state is announced and
   * survives a reader who cannot see the fill. Select all / Clear all is one
   * button whose label flips, matching the reference.
   */
  function chipGroup(options) {
    var o = options || {};
    return {
      name: o.name || '',
      values: o.values || [],
      labels: o.labels || {},
      selected: (o.selected || []).slice(),
      /**
       * A regional-indicator flag for an ISO-2 code, purely decorative.
       * The markup hides it from assistive technology and lets the country name
       * be the accessible label: readers announce flag emoji inconsistently, and
       * on a machine without an emoji font it renders as two letters anyway.
       * UK is not an ISO-2 region code for the flag; GB is.
       */
      flag: function (code) {
        var region = code === 'UK' ? 'GB' : code;
        return String.fromCodePoint.apply(String, region.split('').map(function (ch) {
          return 127397 + ch.charCodeAt(0);
        }));
      },
      label: function (value) {
        return this.labels[value] || value;
      },
      isOn: function (value) {
        return this.selected.indexOf(value) !== -1;
      },
      toggle: function (value, $event) {
        this.selected = this.isOn(value)
          ? this.selected.filter(function (v) { return v !== value; })
          : this.selected.concat([value]);
        emit($event && $event.target, this.name, this.selected);
      },
      get allSelected() {
        return this.selected.length === this.values.length;
      },
      get toggleAllLabel() {
        return this.allSelected ? 'Clear all' : 'Select all';
      },
      toggleAll: function ($event) {
        this.selected = this.allSelected ? [] : this.values.slice();
        emit($event && $event.target, this.name, this.selected);
      },
    };
  }

  /**
   * An on/off switch.
   *
   * A <button role="switch" aria-checked>, which is keyboard-operable and
   * announced as a switch. The knob's position is a second, non-colour signal.
   */
  function switchControl(options) {
    var o = options || {};
    return {
      id: nextId('switch'),
      name: o.name || '',
      on: Boolean(o.on),
      toggle: function ($event) {
        this.on = !this.on;
        emit($event && $event.target, this.name, this.on);
      },
    };
  }

  /**
   * A segmented control — development risk appetite.
   *
   * Real radios inside labels, grouped by a shared `name`, so arrow keys move
   * between options and the group is announced as one. `notes` supplies the
   * line of explanation under the current choice.
   *
   * `options` are canonical values and `labels` carries what the user reads, for
   * the same reason the chips separate the two: the emitted value is a wire value,
   * and a control that emits its own display string is a contract bug waiting to
   * be found downstream.
   */
  function segmented(options) {
    var o = options || {};
    return {
      id: nextId('seg'),
      name: o.name || '',
      group: nextId('seg-group'),
      value: o.value,
      options: o.options || [],
      labels: o.labels || {},
      notes: o.notes || {},
      label: function (value) {
        return this.labels[value] || value;
      },
      get note() {
        return this.notes[this.value] || '';
      },
      changed: function ($event) {
        emit($event && $event.target, this.name, this.value);
      },
    };
  }

  /**
   * The technology split bar.
   *
   * A 30px bar with the two shares as inset fills, a 9px handle on the boundary,
   * and a transparent <input type="range"> stretched across the whole thing —
   * so dragging feels like dragging the bar, while the control underneath is a
   * real slider with real keyboard support. `value` is whole percent solar,
   * which is what the native step works in; the emitted value is a fraction,
   * which is what the mandate stores.
   */
  function splitBar(options) {
    var o = options || {};
    return {
      id: nextId('split'),
      name: o.name || '',
      value: o.value,
      min: 0,
      max: 100,
      step: o.step || 5,
      get solar() { return Math.round(this.value); },
      get wind() { return 100 - Math.round(this.value); },
      get display() { return this.solar + '% solar / ' + this.wind + '% wind'; },
      changed: function ($event) {
        emit($event && $event.target, this.name, this.value / 100);
      },
    };
  }

  /**
   * A KPI tile's compliance state.
   *
   * The mockup expressed this purely as the sub-label's colour, which epic §5
   * forbids. Each state therefore carries a mark and a spoken label as well.
   */
  var TILE_STATES = {
    'on-target': { mark: '✓', label: 'on target' },
    neutral: { mark: '', label: '' },
    breach: { mark: '!', label: 'outside the mandate' },
  };

  function kpiTile(options) {
    var o = options || {};
    return {
      state: o.state || 'neutral',
      get mark() { return (TILE_STATES[this.state] || TILE_STATES.neutral).mark; },
      get stateLabel() { return (TILE_STATES[this.state] || TILE_STATES.neutral).label; },
      get subClass() {
        return this.state === 'breach' ? 'text-breach'
          : this.state === 'on-target' ? 'text-accent-700'
            : 'text-muted';
      },
    };
  }

  var factories = {
    rangeField: rangeField,
    numberField: numberField,
    chipGroup: chipGroup,
    switchControl: switchControl,
    segmented: segmented,
    splitBar: splitBar,
    kpiTile: kpiTile,
  };

  root.TerraFolio = root.TerraFolio || {};
  root.TerraFolio.controls = factories;

  /* Alpine may load before or after this file; alpine:init covers both. */
  if (root.document) {
    root.document.addEventListener('alpine:init', function () {
      Object.keys(factories).forEach(function (name) {
        root.Alpine.data(name, factories[name]);
      });
    });
  }

  if (typeof module === 'object' && module.exports) module.exports = factories;
})(typeof globalThis !== 'undefined' ? globalThis : this);
