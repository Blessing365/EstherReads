(function () {
  function initialize(root) {
    root.querySelectorAll("[data-rich-text-editor]").forEach(editor => {
      if (editor.dataset.editorReady) return;
      editor.dataset.editorReady = "true";
      const toolbar = editor.parentElement.querySelector("[data-editor-toolbar]");
      toolbar.querySelectorAll("[data-command]").forEach(button => {
        button.addEventListener("mousedown", event => event.preventDefault());
        button.addEventListener("click", () => {
          editor.focus();
          document.execCommand(button.dataset.command, false);
          editor.dispatchEvent(new Event("input", { bubbles: true }));
        });
      });
    });
  }

  function value(editor) {
    return editor.innerHTML.trim();
  }

  window.storyEditor = { initialize, value };
})();
