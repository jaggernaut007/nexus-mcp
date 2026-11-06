"""C++ symbol names: out-of-line methods, nested classes, `::` lookups (Chromium `base/`).

The fixtures are cut down from `base/memory/weak_ptr.h`, `weak_ptr.cc` and
`base/at_exit.h`. A benchmark run on Chromium showed the defects: `GetRef` was indexed
under its return type `WeakReference`, and `WeakReference::Flag` was not found.
"""

import asyncio
import time

import pytest

from nexus_mcp import core_api
from nexus_mcp.core.models import SymbolType
from nexus_mcp.parsing.treesitter_parser import TreeSitterParser
from tests.conftest import _setup_indexed

WEAK_PTR_H = """\
// Weak pointers are pointers to an object that do not affect its lifetime.
namespace base {
class SequencedTaskRunner;
namespace internal {
class WeakReference {
 public:
  class Flag {
   public:
    Flag();
    void Invalidate();
    bool IsValid() const { return valid_marker_ != 0; }
   private:
    int valid_marker_ = 1;
  };
  WeakReference();
  explicit WeakReference(const scoped_refptr<Flag>& flag);
  ~WeakReference();
  WeakReference& operator=(WeakReference&& other) { return *this; }
  bool IsValid() const;
  template <typename T>
  T* Cast() const { return static_cast<T*>(nullptr); }
 private:
  scoped_refptr<const Flag> flag_;
};
class WeakReferenceOwner {
 public:
  WeakReference GetRef() const;
};
}  // namespace internal
}  // namespace base
"""

WEAK_PTR_CC = """\
namespace base {
namespace internal {
WeakReference::Flag::Flag() = default;
void WeakReference::Flag::Invalidate() { valid_marker_ = 0; }
WeakReference::WeakReference() = default;
WeakReference::WeakReference(const scoped_refptr<Flag>& flag) : flag_(flag) {}
WeakReference::~WeakReference() = default;
bool WeakReference::IsValid() const { return flag_ && flag_->IsValid(); }
WeakReference WeakReferenceOwner::GetRef() const {
  return WeakReference(flag_for_get_ref_);
}
bool operator==(const WeakReference& a, const WeakReference& b) { return true; }
template <typename T>
T* MakeRaw(T* p) { return p; }
template <typename T>
void Holder<T>::Reset() { held_ = nullptr; }
const char* FreeName(int x) { return "free"; }
}  // namespace internal
}  // namespace base
"""

AT_EXIT_H = """\
namespace base {
class AtExitManager {
 public:
  static void RegisterCallback(AtExitCallbackType func, void* param) {
    RegisterTask(BindOnce(func, param));
  }
};
}  // namespace base
"""

PLAIN_C_H = """\
#ifndef PLAIN_H
#define PLAIN_H
typedef unsigned long size_type;
size_type count_items(const char* s);
#endif
"""

PLAIN_C = """\
typedef unsigned long size_type;
size_type count_items(const char* s) { return 0; }
"""


@pytest.fixture
def parser():
    return TreeSitterParser()


@pytest.fixture
def cpp_tree(tmp_path):
    (tmp_path / "weak_ptr.h").write_text(WEAK_PTR_H)
    (tmp_path / "weak_ptr.cc").write_text(WEAK_PTR_CC)
    (tmp_path / "at_exit.h").write_text(AT_EXIT_H)
    return tmp_path


def _symbols(parser, path):
    """(qualified name, type) of each symbol of a file, without the module symbol."""
    parsed = parser.parse_file(str(path))
    assert parsed.error is None, parsed.error
    return [(s.qualified_name, s.type) for s in parsed.symbols if s.type != SymbolType.MODULE]


def _by_line(parser, path, line):
    return [
        s for s in parser.parse_file(str(path)).symbols
        if s.line_start == line and s.type != SymbolType.MODULE
    ]


class TestOutOfLineDefinitions:
    def test_parse_file_out_of_line_method_named_after_its_function_not_its_return_type(
        self, parser, cpp_tree
    ):
        (sym,) = _by_line(parser, cpp_tree / "weak_ptr.cc", 9)
        assert sym.name == "GetRef"
        assert sym.parent == "WeakReferenceOwner"
        assert sym.type == SymbolType.METHOD

    def test_parse_file_out_of_line_methods_are_all_indexed(self, parser, cpp_tree):
        names = [n for n, _ in _symbols(parser, cpp_tree / "weak_ptr.cc")]
        for expected in (
            "WeakReference.Flag.Flag",
            "WeakReference.Flag.Invalidate",
            "WeakReference.WeakReference",
            "WeakReference.~WeakReference",
            "WeakReference.IsValid",
            "WeakReferenceOwner.GetRef",
        ):
            assert expected in names, names

    def test_parse_file_constructor_and_destructor_are_methods(self, parser, cpp_tree):
        syms = dict(_symbols(parser, cpp_tree / "weak_ptr.cc"))
        assert syms["WeakReference.WeakReference"] == SymbolType.METHOD
        assert syms["WeakReference.~WeakReference"] == SymbolType.METHOD

    def test_parse_file_free_operator_template_and_pointer_functions_keep_their_names(
        self, parser, cpp_tree
    ):
        syms = dict(_symbols(parser, cpp_tree / "weak_ptr.cc"))
        assert syms.get("operator==") == SymbolType.FUNCTION, syms
        assert syms.get("MakeRaw") == SymbolType.FUNCTION, syms
        assert syms.get("FreeName") == SymbolType.FUNCTION, syms
        assert "Holder.Reset" in syms, syms  # template arguments leave the scope
        assert "T" not in syms and "WeakReference" not in syms, syms


class TestHeaders:
    def test_parse_file_cpp_header_nested_class_has_its_outer_class_as_parent(
        self, parser, cpp_tree
    ):
        syms = dict(_symbols(parser, cpp_tree / "weak_ptr.h"))
        assert syms.get("WeakReference") == SymbolType.CLASS, syms
        assert syms.get("WeakReference.Flag") == SymbolType.CLASS, syms
        assert syms.get("WeakReferenceOwner") == SymbolType.CLASS, syms
        assert syms.get("WeakReference.Flag.IsValid") == SymbolType.METHOD, syms

    def test_parse_file_cpp_header_inline_operator_and_template_methods(self, parser, cpp_tree):
        syms = dict(_symbols(parser, cpp_tree / "weak_ptr.h"))
        assert syms.get("WeakReference.operator=") == SymbolType.METHOD, syms
        assert syms.get("WeakReference.Cast") == SymbolType.METHOD, syms

    def test_parse_file_cpp_header_has_no_keyword_or_forward_declaration_symbols(
        self, parser, cpp_tree
    ):
        names = [n for n, _ in _symbols(parser, cpp_tree / "weak_ptr.h")]
        assert "namespace" not in names and "class" not in names, names
        assert "SequencedTaskRunner" not in names, names  # `class X;` declares, not defines
        assert names.count("WeakReference") == 1, names

    def test_parse_file_cpp_header_is_parsed_as_cpp(self, parser, cpp_tree):
        assert parser.parse_file(str(cpp_tree / "weak_ptr.h")).language == "cpp"

    def test_parse_file_plain_c_header_stays_c(self, parser, tmp_path):
        path = tmp_path / "plain.h"
        path.write_text(PLAIN_C_H)
        assert parser.parse_file(str(path)).language == "c"

    def test_parse_file_c_function_named_after_its_function_not_its_typedef_return(
        self, parser, tmp_path
    ):
        path = tmp_path / "plain.c"
        path.write_text(PLAIN_C)
        names = [n for n, _ in _symbols(parser, path)]
        assert "count_items" in names and "size_type" not in names, names


class TestQualifiedLookup:
    def _index(self, codebase, tmp_path):
        asyncio.run(_setup_indexed(codebase, tmp_path / ".nexus"))

    def test_find_symbol_cpp_nested_class_by_scope_operator(self, cpp_tree, tmp_path):
        self._index(cpp_tree, tmp_path)
        result = core_api.find_symbol("WeakReference::Flag")
        assert "error" not in result, result
        assert result["symbols"][0]["type"] == "class"
        assert "void Invalidate();" in result["symbols"][0]["code"]

    def test_find_symbol_cpp_method_by_scope_operator(self, cpp_tree, tmp_path):
        self._index(cpp_tree, tmp_path)
        result = core_api.find_symbol("AtExitManager::RegisterCallback, WeakReferenceOwner::GetRef")
        assert "not_found" not in result, result
        codes = [s["code"] for s in result["symbols"]]
        assert any("RegisterTask(BindOnce(func, param))" in c for c in codes), codes
        assert any("flag_for_get_ref_" in c for c in codes), codes

    def test_find_symbol_cpp_bare_class_name_gives_the_class_first(self, cpp_tree, tmp_path):
        self._index(cpp_tree, tmp_path)
        result = core_api.find_symbol("WeakReference")
        assert "error" not in result, result
        first = result["symbols"][0]
        assert first["type"] == "class" and first["file"].endswith("weak_ptr.h"), result
        assert not any("flag_for_get_ref_" in s.get("code", "") for s in result["symbols"])

    def test_find_symbol_cpp_inner_scope_suffix_matches(self, cpp_tree, tmp_path):
        self._index(cpp_tree, tmp_path)
        result = core_api.find_symbol("Flag::Invalidate")
        assert "error" not in result, result
        assert "valid_marker_ = 0" in result["symbols"][0]["code"]


# Export and attribute macros in a class head, as in Chromium `base/` headers
# (`base/timer/timer.h`, `base/memory/weak_ptr.h`, `base/synchronization/lock.h`).
MACRO_CLASSES_H = """\
namespace base {
namespace internal {
class BASE_EXPORT TimerBase {
 public:
  void Stop() { is_running_ = false; }
  class Delegate {
   public:
    void Fire() {}
  };
 private:
  bool is_running_ = false;
};
}  // namespace internal
class BASE_EXPORT DeadlineTimer : public internal::TimerBase {
 public:
  DeadlineTimer() = default;
  void Start() { deadline_started_ = true; }
};
class BASE_EXPORT TRIVIAL_ABI WeakReference {
 public:
  class BASE_EXPORT Flag {
   public:
    void Invalidate() { invalidated_flag_ = true; }
  };
  bool IsValid() const { return true; }
};
class LOCKABLE BASE_EXPORT Lock {
 public:
  void Acquire() EXCLUSIVE_LOCK_FUNCTION() {}
};
class COMPONENT_EXPORT(BASE_CLOCK) TickClock final : public Clock {
 public:
  int Now() const override { return 0; }
};
struct BASE_EXPORT TaskTraits {
  int priority = 0;
};
class URL_PARSER : public Parser {
 public:
  void Parse() {}
};
}  // namespace base
"""


class TestExportMacros:
    @pytest.fixture
    def macro_header(self, tmp_path):
        path = tmp_path / "timer.h"
        path.write_text(MACRO_CLASSES_H)
        return path

    def test_parse_file_class_with_one_export_macro_is_a_class(self, parser, macro_header):
        syms = dict(_symbols(parser, macro_header))
        assert syms.get("TimerBase") == SymbolType.CLASS, syms
        assert syms.get("TimerBase.Stop") == SymbolType.METHOD, syms
        assert "BASE_EXPORT" not in syms, syms

    def test_parse_file_class_with_two_macros_is_a_class(self, parser, macro_header):
        syms = dict(_symbols(parser, macro_header))
        assert syms.get("WeakReference") == SymbolType.CLASS, syms
        assert syms.get("Lock") == SymbolType.CLASS, syms
        assert syms.get("Lock.Acquire") == SymbolType.METHOD, syms
        assert not any("TRIVIAL_ABI" in n for n in syms), syms

    def test_parse_file_class_with_macro_arguments_and_final_is_a_class(
        self, parser, macro_header
    ):
        syms = dict(_symbols(parser, macro_header))
        assert syms.get("TickClock") == SymbolType.CLASS, syms
        assert syms.get("TickClock.Now") == SymbolType.METHOD, syms
        assert not any("COMPONENT_EXPORT" in n or "final" in n for n in syms), syms

    def test_parse_file_class_with_macro_and_base_clause_is_named_after_itself(
        self, parser, macro_header
    ):
        syms = dict(_symbols(parser, macro_header))
        assert syms.get("DeadlineTimer") == SymbolType.CLASS, syms
        assert syms.get("DeadlineTimer.Start") == SymbolType.METHOD, syms
        assert "internal.TimerBase" not in syms, syms

    def test_parse_file_nested_class_in_a_macro_class_has_its_parent(self, parser, macro_header):
        syms = dict(_symbols(parser, macro_header))
        assert syms.get("TimerBase.Delegate") == SymbolType.CLASS, syms
        assert syms.get("TimerBase.Delegate.Fire") == SymbolType.METHOD, syms
        assert syms.get("WeakReference.Flag") == SymbolType.CLASS, syms
        assert syms.get("WeakReference.Flag.Invalidate") == SymbolType.METHOD, syms

    def test_parse_file_struct_with_export_macro_is_a_class(self, parser, macro_header):
        syms = dict(_symbols(parser, macro_header))
        assert syms.get("TaskTraits") == SymbolType.CLASS, syms

    def test_parse_file_all_caps_class_name_is_not_taken_for_a_macro(self, parser, macro_header):
        syms = dict(_symbols(parser, macro_header))
        assert syms.get("URL_PARSER") == SymbolType.CLASS, syms

    def test_parse_file_macro_class_keeps_the_macro_in_its_code(self, parser, macro_header):
        (sym,) = [
            s for s in parser.parse_file(str(macro_header)).symbols if s.name == "WeakReference"
        ]
        assert sym.type == SymbolType.CLASS
        assert sym.signature == "class BASE_EXPORT TRIVIAL_ABI WeakReference {"
        assert sym.line_start == 19

    def test_find_symbol_nested_class_of_a_macro_class(self, macro_header, tmp_path):
        asyncio.run(_setup_indexed(macro_header.parent, tmp_path / ".nexus"))
        result = core_api.find_symbol("WeakReference::Flag")
        assert "error" not in result, result
        assert result["symbols"][0]["type"] == "class"
        assert "invalidated_flag_" in result["symbols"][0]["code"]

    def test_parse_file_method_with_annotation_macro_is_named_after_itself(
        self, parser, tmp_path
    ):
        path = tmp_path / "lock.h"
        path.write_text(
            "class Lock {\n public:\n"
            "  void Acquire() EXCLUSIVE_LOCK_FUNCTION() { a(); }\n"
            "  void Release() UNLOCK_FUNCTION(lock_) { b(); }\n"
            "  bool Try() const EXCLUSIVE_TRYLOCK_FUNCTION(true) { return c(); }\n"
            "  void AssertAcquired() const ASSERT_EXCLUSIVE_LOCK() {}\n"
            "  void Check() { if (ok_) DCHECK(ready_); }\n"
            "};\n"
        )
        syms = dict(_symbols(parser, path))
        for name in ("Acquire", "Release", "Try", "AssertAcquired", "Check"):
            assert syms.get(f"Lock.{name}") == SymbolType.METHOD, syms
        assert not any(n.split(".")[-1].isupper() for n in syms), syms

    def test_parse_file_class_with_attributes_and_macros_is_a_class(self, parser, tmp_path):
        # base/synchronization/lock_impl.h, base/task/task_annotator.h,
        # base/threading/scoped_blocking_call.h: `[[...]]` before, between or after macros.
        path = tmp_path / "scoped.h"
        path.write_text(
            "namespace base {\n"
            "class [[nodiscard]] SCOPED_LOCKABLE BasicAutoLock {\n public:\n"
            "  explicit BasicAutoLock(Lock& lock) EXCLUSIVE_LOCK_FUNCTION(lock)\n"
            "      : lock_(lock) {}\n"
            "};\n"
            "class [[nodiscard]] SCOPED_LOCKABLE BASE_EXPORT TaggedAutoLock {\n public:\n"
            "  void Release() UNLOCK_FUNCTION() {}\n"
            "};\n"
            "class BASE_EXPORT [[maybe_unused, nodiscard]] TaskScope {\n public:\n"
            "  void Enter() {}\n"
            "};\n"
            "class BASE_EXPORT [[nodiscard]] ScopedBlockingCall\n"
            "    : public internal::UncheckedScopedBlockingCall {\n public:\n"
            "  void Wait() {}\n"
            "};\n"
            "}  // namespace base\n"
        )
        syms = dict(_symbols(parser, path))
        for cls, method in (
            ("BasicAutoLock", "BasicAutoLock"),
            ("TaggedAutoLock", "Release"),
            ("TaskScope", "Enter"),
            ("ScopedBlockingCall", "Wait"),
        ):
            assert syms.get(cls) == SymbolType.CLASS, syms
            assert syms.get(f"{cls}.{method}") == SymbolType.METHOD, syms
        assert not any(n.split(".")[-1].isupper() for n in syms), syms

    def test_parse_file_out_of_line_nested_class_is_named_like_a_nested_class(
        self, parser, tmp_path
    ):
        # base/allocator/dispatcher/dispatcher.cc, base/task/common/task_annotator.h:
        # `class Outer::Inner {` gets the name `Inner` and the parent `Outer`, as a class
        # nested in the body of `Outer` does, so `find_symbol("Outer::Inner")` finds it.
        path = tmp_path / "dispatcher.cc"
        path.write_text(
            "namespace base {\n"
            "class Dispatcher::Impl {\n public:\n  void Reset() {}\n};\n"
            "class BASE_EXPORT [[maybe_unused, nodiscard]] TaskAnnotator::LongTaskTracker {\n"
            " public:\n  void Stop() {}\n};\n"
            "}  // namespace base\n"
            "template <typename T>\n"
            "struct std::pointer_traits<base::CheckedIterator<T>> {\n"
            "  static T* to_address(T p) { return p; }\n};\n"
        )
        parsed = {s.qualified_name: s for s in parser.parse_file(str(path)).symbols}
        assert parsed["Dispatcher.Impl"].type == SymbolType.CLASS, list(parsed)
        assert parsed["Dispatcher.Impl"].name == "Impl"
        assert parsed["Dispatcher.Impl"].parent == "Dispatcher"
        assert parsed["Dispatcher.Impl.Reset"].type == SymbolType.METHOD
        assert parsed["TaskAnnotator.LongTaskTracker"].type == SymbolType.CLASS
        assert parsed["TaskAnnotator.LongTaskTracker.Stop"].type == SymbolType.METHOD
        # A `::` inside template arguments does not split the name.
        traits = parsed["std.pointer_traits<base::CheckedIterator<T>>"]
        assert traits.type == SymbolType.CLASS and traits.parent == "std"
        assert not any("::" in n for n in parsed if "<" not in n), list(parsed)

    def test_parse_file_constructor_with_annotation_macro_and_initializer(
        self, parser, tmp_path
    ):
        # base/synchronization/lock_impl.h: the macro comes before a member initializer list.
        path = tmp_path / "lock_impl.h"
        path.write_text(
            "class BASE_EXPORT AutoLock {\n public:\n"
            "  explicit AutoLock(Lock& lock) EXCLUSIVE_LOCK_FUNCTION(lock)\n"
            "      : lock_(lock) {\n    lock_.Acquire();\n  }\n"
            "  AutoLock(Lock& lock, const AlreadyAcquired&) EXCLUSIVE_LOCKS_REQUIRED(lock)"
            " : lock_(lock) {}\n"
            "};\n"
            "AutoLock::AutoLock(int n) NO_THREAD_SAFETY_ANALYSIS : n_(n) {}\n"
        )
        syms = _symbols(parser, path)
        names = [n for n, _ in syms]
        assert names.count("AutoLock.AutoLock") == 3, syms
        assert not any(n.split(".")[-1].isupper() for n in names), syms

    def test_parse_file_macro_lines_after_a_paren_do_not_backtrack(self, parser, tmp_path):
        # base/check_op.h: a run of ALL_CAPS macro calls after a `)`. Before the fix each
        # macro name could be split into many shorter "macros", and four such lines took
        # 5 s in the tail-macro regex; 23 files of base/ stopped the index for over an hour.
        path = tmp_path / "check_op.h"
        path.write_text(
            "namespace base {\n"
            "void Check() {}\n"
            "DEFINE_CHECK_OP_IMPL(EQ, ==)\n"
            "DEFINE_CHECK_OP_IMPL(NE, !=)\n"
            "DEFINE_CHECK_OP_IMPL(LE, <=)\n"
            "DEFINE_CHECK_OP_IMPL(LT, < )\n"
            "}  // namespace base\n"
        )
        _symbols(parser, path)  # load the C++ grammar outside the timed call
        start = time.perf_counter()
        syms = dict(_symbols(parser, path))
        assert time.perf_counter() - start < 0.5
        assert syms.get("Check") == SymbolType.FUNCTION, syms


def test_parse_file_cpp_keyword_is_not_a_symbol_name(tmp_path):
    # fmt: `FMT_BEGIN_NAMESPACE namespace detail {` gave a function named `namespace`.
    path = tmp_path / "impl.cc"
    path.write_text("FMT_BEGIN_NAMESPACE namespace detail {\nvoid parse_width() { }\n}\n")
    names = {s.name for s in TreeSitterParser().parse_file(str(path)).symbols}
    assert "namespace" not in names
    assert "parse_width" in names
