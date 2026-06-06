<!-- scryo: searchable combobox (typeahead) — drop-in for Select when the option list is long. -->
<script lang="ts">
  interface Option {
    value: string | undefined;
    label: string;
  }

  interface Props {
    label?: string | null;
    value: string | null | undefined;
    options: Option[];
    onChange: (value: string | undefined) => void;
    placeholder?: string;
    class?: string;
  }

  let { label = null, value, options, onChange, placeholder = "Search…", class: klass = "" }: Props =
    $props();

  let open = $state(false);
  let query = $state("");
  let highlight = $state(0);
  let inputEl: HTMLInputElement | undefined = $state();

  let currentLabel = $derived(options.find((o) => o.value === value)?.label ?? "--");

  let filtered = $derived.by(() => {
    let q = query.toLowerCase().trim();
    if (q.length === 0) return options.slice(0, 100);
    let starts = options.filter((o) => o.label.toLowerCase().startsWith(q));
    let contains = options.filter(
      (o) => !o.label.toLowerCase().startsWith(q) && o.label.toLowerCase().includes(q),
    );
    return [...starts, ...contains].slice(0, 100);
  });

  function choose(opt: Option) {
    onChange(opt.value);
    open = false;
    query = "";
    inputEl?.blur();
  }

  function openDropdown() {
    open = true;
    query = "";
    highlight = 0;
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      highlight = Math.min(highlight + 1, filtered.length - 1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      highlight = Math.max(highlight - 1, 0);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (highlight >= 0 && highlight < filtered.length) choose(filtered[highlight]);
    } else if (e.key === "Escape") {
      open = false;
      query = "";
      inputEl?.blur();
    }
  }
</script>

<label class="select-none flex items-center gap-2 {klass}">
  {#if label != null}
    <span class="text-slate-500 dark:text-slate-400 whitespace-nowrap">{label}</span>
  {/if}
  <div class="relative">
    <input
      bind:this={inputEl}
      type="text"
      class="form-input w-full rounded-md py-1 px-2 bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-600 dark:text-slate-300 text-ellipsis cursor-pointer"
      value={open ? query : currentLabel}
      placeholder={placeholder}
      onfocus={openDropdown}
      onpointerdown={openDropdown}
      oninput={(e) => {
        query = (e.target as HTMLInputElement).value;
        open = true;
        highlight = 0;
      }}
      onkeydown={handleKeydown}
      onblur={() => setTimeout(() => (open = false), 120)}
    />
    {#if open && filtered.length > 0}
      <div
        class="absolute z-50 mt-1 w-full min-w-40 max-h-60 overflow-y-auto rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 shadow-lg"
      >
        {#each filtered as opt, i}
          <button
            type="button"
            class="w-full px-3 py-1 text-left text-sm hover:bg-blue-50 dark:hover:bg-slate-700 transition-colors"
            class:bg-blue-50={i === highlight}
            class:dark:bg-slate-700={i === highlight}
            class:font-semibold={opt.value === value}
            onpointerdown={(e: MouseEvent) => {
              e.preventDefault();
              choose(opt);
            }}
          >
            {opt.label}
          </button>
        {/each}
      </div>
    {/if}
  </div>
</label>
