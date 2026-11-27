// Tiny DOM builders. Every label that reaches the page goes through
// `textContent` (strings become text nodes), never through innerHTML: product
// names, rule descriptions and event titles come from JSON files.

const SVG_NS = "http://www.w3.org/2000/svg";

function applyAttributes(element, attributes) {
  if (!attributes) return;
  for (const [name, value] of Object.entries(attributes)) {
    if (value == null || value === false) continue;
    if (name === "style" && typeof value === "object") {
      for (const [property, v] of Object.entries(value)) {
        if (v == null) continue;
        if (property.startsWith("--")) element.style.setProperty(property, v);
        else element.style[property] = v;
      }
    } else if (name === "dataset") {
      Object.assign(element.dataset, value);
    } else if (name.startsWith("on") && typeof value === "function") {
      element.addEventListener(name.slice(2).toLowerCase(), value);
    } else if (name === "text") {
      element.textContent = value;
    } else if (value === true) {
      element.setAttribute(name, "");
    } else {
      element.setAttribute(name, value);
    }
  }
}

function appendChildren(element, children) {
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false || child === "") continue;
    element.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

/** Create an HTML element: h("p", {class: "note"}, "text", otherNode). */
export function h(tag, attributes, ...children) {
  const element = document.createElement(tag);
  applyAttributes(element, attributes);
  appendChildren(element, children);
  return element;
}

/** Create an SVG element. */
export function s(tag, attributes, ...children) {
  const element = document.createElementNS(SVG_NS, tag);
  applyAttributes(element, attributes);
  appendChildren(element, children);
  return element;
}

/** A URL from the data files is used as a link only when it is plain http(s). */
export function safeUrl(url) {
  return typeof url === "string" && /^https?:\/\//i.test(url) ? url : null;
}

/** Run `fn` at most once per animation frame with the latest arguments. */
export function rafThrottle(fn) {
  let pending = null;
  let scheduled = false;
  return (...args) => {
    pending = args;
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      fn(...pending);
    });
  };
}

export function debounce(fn, wait) {
  let timer = 0;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}
