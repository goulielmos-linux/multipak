.pragma library

function source(name, background) {
    const brightness = 0.2126 * background.r + 0.7152 * background.g + 0.0722 * background.b;
    const variant = brightness < 0.5 ? "dark" : "light";
    return Qt.resolvedUrl("assets/breeze/" + variant + "/" + name + ".svg");
}
