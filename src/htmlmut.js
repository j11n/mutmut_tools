
function toggle(element) {
    const paragraph = document.getElementById(element);
    if (paragraph.style.display === 'none') {
        paragraph.style.display = "block";
    }
    else {
        paragraph.style.display = "none";
    }
}
