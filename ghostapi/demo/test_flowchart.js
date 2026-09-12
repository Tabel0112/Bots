const assert = require('node:assert/strict');
const test = require('node:test');
const { bindPanning } = require('./flowchart.js');

function fixture() {
  const classes = new Set();
  const view = {
    scrollLeft: 100,
    scrollTop: 200,
    captured: null,
    classList: {
      add(value) { classes.add(value); },
      remove(value) { classes.delete(value); },
      contains(value) { return classes.has(value); },
    },
    setPointerCapture(pointerId) { this.captured = pointerId; },
    hasPointerCapture(pointerId) { return this.captured === pointerId; },
    releasePointerCapture() { this.captured = null; },
  };
  return view;
}

function pointer(overrides = {}) {
  return {
    button: 0,
    pointerId: 7,
    clientX: 10,
    clientY: 20,
    target: { closest() { return null; } },
    prevented: false,
    preventDefault() { this.prevented = true; },
    ...overrides,
  };
}

test('primary-pointer drag pans the chart and reports manual control', () => {
  const view = fixture();
  let manualPans = 0;
  const controller = bindPanning(view, () => { manualPans += 1; });
  const down = pointer();

  view.onpointerdown(down);
  assert.equal(down.prevented, true);
  assert.equal(view.captured, 7);
  assert.equal(view.classList.contains('dragging'), true);
  assert.equal(controller.isDragging(), true);
  assert.equal(manualPans, 1);

  view.onpointermove(pointer({ clientX: 40, clientY: 55 }));
  assert.equal(view.scrollLeft, 70);
  assert.equal(view.scrollTop, 165);

  view.onpointerup(pointer());
  assert.equal(view.captured, null);
  assert.equal(view.classList.contains('dragging'), false);
  assert.equal(controller.isDragging(), false);
});

test('controls and non-primary mouse buttons do not start panning', () => {
  const view = fixture();
  const controller = bindPanning(view);
  const control = pointer({ target: { closest() { return {}; } } });
  const rightClick = pointer({ button: 2 });

  view.onpointerdown(control);
  view.onpointerdown(rightClick);

  assert.equal(control.prevented, false);
  assert.equal(rightClick.prevented, false);
  assert.equal(controller.isDragging(), false);
  assert.equal(view.captured, null);
});

test('unrelated pointer events cannot move or finish an active drag', () => {
  const view = fixture();
  const controller = bindPanning(view);
  view.onpointerdown(pointer());

  view.onpointermove(pointer({ pointerId: 99, clientX: 500, clientY: 500 }));
  view.onpointerup(pointer({ pointerId: 99 }));

  assert.equal(view.scrollLeft, 100);
  assert.equal(view.scrollTop, 200);
  assert.equal(controller.isDragging(), true);
  assert.equal(view.captured, 7);
});

test('cancel and lost capture always clear drag state', () => {
  const view = fixture();
  const controller = bindPanning(view);
  view.onpointerdown(pointer());
  view.onpointercancel(pointer());
  assert.equal(controller.isDragging(), false);

  view.onpointerdown(pointer());
  view.onlostpointercapture();
  assert.equal(controller.isDragging(), false);
  assert.equal(view.classList.contains('dragging'), false);
});

test('destroy removes handlers and active styling', () => {
  const view = fixture();
  const controller = bindPanning(view);
  view.onpointerdown(pointer());
  controller.destroy();

  assert.equal(controller.isDragging(), false);
  assert.equal(view.classList.contains('dragging'), false);
  assert.equal(view.onpointerdown, null);
  assert.equal(view.onpointermove, null);
  assert.equal(view.onpointerup, null);
  assert.equal(view.onpointercancel, null);
  assert.equal(view.onlostpointercapture, null);
});
