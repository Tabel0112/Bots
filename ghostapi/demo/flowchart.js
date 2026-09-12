(function exposeFlowchartControls(root, factory) {
  const controls = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = controls;
  } else {
    root.GhostFlow = controls;
  }
}(typeof globalThis === 'object' ? globalThis : this, function createFlowchartControls() {
  function bindPanning(view, onManualPan) {
    let pan = null;
    const notify = typeof onManualPan === 'function' ? onManualPan : function noop() {};

    function isInteractive(target) {
      return Boolean(target && typeof target.closest === 'function'
        && target.closest('button,input,select,summary'));
    }

    function finish(event) {
      if (!pan || event.pointerId !== pan.pointerId) return;
      if (typeof view.hasPointerCapture === 'function'
          && view.hasPointerCapture(event.pointerId)) {
        view.releasePointerCapture(event.pointerId);
      }
      view.classList.remove('dragging');
      pan = null;
    }

    view.onpointerdown = function start(event) {
      if (event.button !== 0 || isInteractive(event.target)) return;
      pan = {
        pointerId: event.pointerId,
        x: event.clientX,
        y: event.clientY,
        left: view.scrollLeft,
        top: view.scrollTop,
      };
      view.setPointerCapture(event.pointerId);
      view.classList.add('dragging');
      notify();
      event.preventDefault();
    };

    view.onpointermove = function move(event) {
      if (!pan || event.pointerId !== pan.pointerId) return;
      view.scrollLeft = pan.left - (event.clientX - pan.x);
      view.scrollTop = pan.top - (event.clientY - pan.y);
    };

    view.onpointerup = finish;
    view.onpointercancel = finish;
    view.onlostpointercapture = function lostCapture() {
      view.classList.remove('dragging');
      pan = null;
    };

    return {
      isDragging: function isDragging() { return pan !== null; },
      destroy: function destroy() {
        view.onpointerdown = null;
        view.onpointermove = null;
        view.onpointerup = null;
        view.onpointercancel = null;
        view.onlostpointercapture = null;
        view.classList.remove('dragging');
        pan = null;
      },
    };
  }

  return { bindPanning: bindPanning };
}));
