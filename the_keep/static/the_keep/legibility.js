// Color legibility helpers — mirror of the Python helpers in
// the_forge/pdf_engine.py (_relative_luminance, _contrast_ratio,
// _is_white_text_legible). Same 1.9 large-text threshold so live preview
// matches PDF output.

(function (root) {
    function hexToRgb(hex) {
        hex = String(hex || '').replace(/^#/, '');
        if (hex.length === 3) {
            hex = hex.split('').map(function (c) { return c + c; }).join('');
        }
        return {
            r: parseInt(hex.slice(0, 2), 16),
            g: parseInt(hex.slice(2, 4), 16),
            b: parseInt(hex.slice(4, 6), 16),
        };
    }

    function luminance(r, g, b) {
        var a = [r, g, b].map(function (value) {
            value = value / 255;
            return value <= 0.03928 ? value / 12.92 : Math.pow((value + 0.055) / 1.055, 2.4);
        });
        return 0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2];
    }

    function contrastRatio(colorHex) {
        var rgb = hexToRgb(colorHex);
        if (isNaN(rgb.r) || isNaN(rgb.g) || isNaN(rgb.b)) return 1;
        var L2 = luminance(rgb.r, rgb.g, rgb.b);
        var L1 = 1;
        return (L1 + 0.05) / (L2 + 0.05);
    }

    function isWhiteTextLegible(colorHex, isLargeText) {
        if (isLargeText === undefined) isLargeText = true;
        var ratio = contrastRatio(colorHex);
        return ratio >= (isLargeText ? 1.9 : 4.5);
    }

    root.hexToRgb = hexToRgb;
    root.luminance = luminance;
    root.contrastRatio = contrastRatio;
    root.isWhiteTextLegible = isWhiteTextLegible;
})(window);
