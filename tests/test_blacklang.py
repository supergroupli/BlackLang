# -*- coding: utf-8 -*-
"""BlackLang MVP 解释器测试（标准库 unittest，零依赖）。"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from blacklang.evaluator import BlackRuntimeError, Evaluator
from blacklang.parser import parse, ParseError
from blacklang.tokenizer import tokenize, LexError


def exec_source(source: str) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        Evaluator(parse(source)).run()
    return buf.getvalue()


class TestTokenizer(unittest.TestCase):
    def test_comments_skipped(self):
        toks = tokenize("// hi\nlet a = 1;")
        kinds = [t.value for t in toks if t.kind != "EOF"]
        self.assertNotIn("/", kinds)

    def test_numbers(self):
        toks = [t for t in tokenize("1 3.14") if t.kind != "EOF"]
        self.assertEqual(toks[0].kind, "NUMBER")
        self.assertEqual(toks[1].value, "3.14")

    def test_string_escape(self):
        tok = [t for t in tokenize('"a\\nb"') if t.kind != "EOF"][0]
        self.assertEqual(tok.value, "a\nb")

    def test_lex_error(self):
        with self.assertRaises(LexError):
            tokenize('"unclosed')


class TestParser(unittest.TestCase):
    def test_parse_program(self):
        prog = parse("run main: { print(1); }")
        self.assertTrue(prog.entries)

    def test_parse_error(self):
        with self.assertRaises(ParseError):
            parse("run main: { if }")

    def test_optional_semicolon(self):
        # 末尾不求分号
        prog = parse("run main: { print(1) }")
        self.assertTrue(prog.entries)


class TestEvaluator(unittest.TestCase):
    def test_hello(self):
        out = exec_source('run main: { print("hi"); }')
        self.assertEqual(out.strip(), "hi")

    def test_arithmetic(self):
        out = exec_source('run main: { print(2 + 3 * 4); }')
        self.assertEqual(out.strip(), "14")

    def test_condition(self):
        out = exec_source("run main: { if 3 > 2: { print('yes'); } else: { print('no'); } }")
        self.assertEqual(out.strip(), "yes")

    def test_loop(self):
        out = exec_source("run main: { let s=0; for i in [1,2,3]: { s = s + i; } print(s); }")
        self.assertEqual(out.strip(), "6")

    def test_recursion(self):
        src = """
fn fib(n): {
    if n < 2: { return n; }
    return fib(n-1) + fib(n-2);
}
run main: { print(fib(10)); }
"""
        self.assertEqual(exec_source(src).strip(), "55")

    def test_sandbox_allows_cap(self):
        out = exec_source("""
run main: {
    sandbox readonly-fs: {
        print(type("x"));
    }
}
""")
        self.assertIn("str", out)

    def test_sandbox_blocks_unauthorized(self):
        with self.assertRaises(BlackRuntimeError) as cm:
            exec_source("""
run main: {
    sandbox readonly-fs: {
        let r = http_get("http://x");
    }
}
""")
        self.assertIn("越权调用", str(cm.exception))

    def test_interop_registration(self):
        out = exec_source("""
run main: {
    use python: { numpy };
    print(numpy);
}
""")
        self.assertIn("numpy", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)

class TestStaticChecker(unittest.TestCase):
    def _check(self, source: str):
        from blacklang.checker import static_check
        return static_check(parse(source))

    def test_plain_passes(self):
        _, errors = self._check('run main: { print("hi"); }')
        self.assertEqual(errors, [])

    def test_authorized_net_passes(self):
        _, errors = self._check(
            'run main: { sandbox net: { let r = http_get("http://x"); } }'
        )
        self.assertEqual(errors, [])

    def test_unauthorized_net_rejected_at_compile_time(self):
        checker, errors = self._check(
            'run main: { sandbox readonly-fs: { let r = http_get("http://x"); } }'
        )
        self.assertTrue(any("越权拒绝" in e for e in errors))
        self.assertIn("http_get", checker.manifest.unauthorized)

    def test_manifest_program_caps(self):
        checker, _ = self._check(
            'run main: { sandbox net: { let r = http_get("http://x"); } }'
        )
        self.assertIn("net", checker.manifest.program_caps)

    def test_function_required_caps_collected(self):
        checker, errors = self._check(
            "fn load(): { sandbox readonly-fs: { return read('f'); } }\n"
            "run main: { let c = load(); }"
        )
        self.assertEqual(errors, [])
        self.assertEqual(checker.manifest.per_fn["load"], ["readonly-fs"])


class TestTypeChecker(unittest.TestCase):
    def _errors(self, source: str):
        from blacklang.checker import static_check
        from blacklang.typechecker import type_check
        prog = parse(source)
        checker, ce = static_check(prog)
        return ce + type_check(prog, checker)

    def test_valid_typed_passes(self):
        src = "fn add(x: int, y: int) -> int: { return x + y; } run main: { print(add(1,2)); }"
        self.assertEqual(self._errors(src), [])

    def test_int_float_promotion(self):
        self.assertEqual(self._errors("run main: { print(1 + 2.5); }"), [])

    def test_annotated_type_mismatch(self):
        errs = self._errors('run main: { let s: int = "x"; }')
        self.assertTrue(any("声明为 int" in e for e in errs))

    def test_return_type_mismatch(self):
        errs = self._errors('fn f(x: int) -> string: { return x; } run main: { print(f(1)); }')
        self.assertTrue(any("返回类型不符" in e for e in errs))

    def test_bad_binary_op(self):
        # string + number 允许（AI 友好自动转 string）；string - number 仍报错
        errs = self._errors('run main: { let t = "a" - 1; }')
        self.assertTrue(any("需要数值操作数" in e for e in errs))

    def test_unknown_var(self):
        errs = self._errors("run main: { print(undeclared_thing); }")
        self.assertTrue(any("未定义的名称" in e for e in errs))


class TestInterop(unittest.TestCase):
    def test_call_real_python_module(self):
        src = """
use python: { math };
run main: { print(math.sqrt(144)); }
"""
        self.assertEqual(exec_source(src).strip(), "12.0")

    def test_json_roundtrip(self):
        src = """
use python: { json };
run main: {
    let d = json.dumps({"name": "bl"});
    print(d);
}
"""
        self.assertIn('"name": "bl"', exec_source(src))

    def test_parser_attr_and_subscript(self):
        src = """
use python: { json };
run main: {
    let o = json.loads('{"a": 1}');
    print(o["a"]);
}
"""
        self.assertEqual(exec_source(src).strip(), "1")


class TestGetItemGetAttr(unittest.TestCase):
    def test_list_methods(self):
        src = "run main: { let d=[1,2,3]; print(d.len()); }"
        self.assertEqual(exec_source(src).strip(), "3")

    def test_list_index(self):
        src = "run main: { let d=[10,20,30]; print(d[1]); }"
        self.assertEqual(exec_source(src).strip(), "20")


class TestNodeInterop(unittest.TestCase):
    def _node_available(self):
        import os, shutil
        if os.environ.get("BLACKLANG_NODE") or shutil.which("node"):
            return True
        return os.path.exists(
            "/Users/superlee/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/node/bin/node")

    def test_node_path_call(self):
        if not self._node_available():
            self.skipTest("node 不可用")
        src = """
use node: { path };
run main: { print(path.join("a", "b")); }
"""
        self.assertIn("a/b", exec_source(src))


class TestPythonAPI(unittest.TestCase):
    def _api(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
        from blacklang import api
        return api

    def test_compile_check_ok(self):
        api = self._api()
        r = api.compile_check('run main: { print(1); }')
        self.assertTrue(r["ok"])
        self.assertEqual(r["capabilities"], [])

    def test_compile_check_rejects_unsafe(self):
        api = self._api()
        r = api.compile_check(
            'run main: { sandbox readonly-fs: { let x = http_get("x"); } }'
        )
        self.assertFalse(r["ok"])
        self.assertTrue(r["errors"])

    def test_run_captures_stdout(self):
        api = self._api()
        r = api.run('run main: { print("hi", 2 * 3); }')
        self.assertTrue(r["ok"])
        self.assertIn("hi 6", r["stdout"])

    def test_run_denies_unsafe_before_executing(self):
        api = self._api()
        r = api.run(
            'run main: { sandbox readonly-fs: { let x = http_get("x"); } }'
        )
        self.assertFalse(r["ok"])
        self.assertEqual(r["stdout"], "")   # 未执行


class TestNewSyntax(unittest.TestCase):
    def test_dict_literal_and_subscript(self):
        out = exec_source('run main: { let d={"a": 1, "b": 2}; print(d["a"] + d["b"]); }')
        self.assertEqual(out.strip(), "3")

    def test_attr_getattr(self):
        out = exec_source("run main: { let d=[1,2,3]; print(d.len()); }")
        self.assertEqual(out.strip(), "3")

    def test_type_annotation_runs(self):
        out = exec_source("run main: { let n: int = 7; print(n * 2); }")
        self.assertEqual(out.strip(), "14")


class TestREPLAndScaffold(unittest.TestCase):
    def test_use_optional_semicolon(self):
        # use 语句无尾分号也能解析(REPL 场景)
        src = 'use python: { math }\nrun main: { print(math.sqrt(9)); }'
        self.assertIn("3.0", exec_source(src))

    def test_return_optional_semicolon(self):
        src = 'fn f(): { return 7\n}\nrun main: { print(f()); }'
        self.assertEqual(exec_source(src).strip(), "7")

    def test_scaffold_creates_runnable_project(self):
        import tempfile
        from blacklang.scaffold import scaffold
        with tempfile.TemporaryDirectory() as d:
            created = scaffold(d, "demo")
            self.assertTrue(any(p.endswith("main.bl") for p in created))
            self.assertTrue(any(p.endswith("config.json") for p in created))
            # 生成的 main.bl 可运行且可检查
            main_path = os.path.join(d, "src", "main.bl")
            from blacklang.evaluator import Evaluator
            from blacklang.parser import parse
            with open(main_path, encoding="utf-8") as f:
                src = f.read()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                Evaluator(parse(src)).run()
            self.assertIn("Hello", out.getvalue())


class TestDictAndAttrParsing(unittest.TestCase):
    def test_attr_subscript_dict(self):
        out = exec_source('run main: { let d={"a":[1,2]}; print(d["a"][1]); }')
        self.assertEqual(out.strip(), "2")


class TestVM(unittest.TestCase):
    """字节码 VM 正确性（性能路径）。"""
    def _vm_exec(self, src):
        from blacklang.vm import run_vm
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            run_vm(parse(src))
        return out.getvalue().strip()

    def test_vm_arith(self):
        self.assertEqual(self._vm_exec("run main: { print(2 + 3 * 4); }"), "14")

    def test_vm_condition(self):
        out = self._vm_exec("run main: { if 5 > 2: { print('y'); } else: { print('n'); } }")
        self.assertEqual(out, "y")

    def test_vm_for_sum(self):
        self.assertEqual(self._vm_exec("run main: { let s=0; for i in [1,2,3,4,5]: { s=s+i; } print(s); }"), "15")

    def test_vm_while(self):
        self.assertEqual(self._vm_exec("run main: { let i=0; while i<5: { i=i+1; } print(i); }"), "5")

    def test_vm_recursion(self):
        src = "fn fib(n: int) -> int: { if n<2: { return n; } return fib(n-1)+fib(n-2); } run main: { print(fib(15)); }"
        self.assertEqual(self._vm_exec(src), "610")

    def test_vm_logical_shortcircuit(self):
        out = self._vm_exec("run main: { if 1>0 and 2>1: { print('both'); } }")
        self.assertEqual(out, "both")
        out = self._vm_exec("run main: { if 0>1 or 2>1: { print('or'); } }")
        self.assertEqual(out, "or")

    def test_vm_list_method(self):
        self.assertEqual(self._vm_exec("run main: { let d=[1,2,3]; print(d.len()); }"), "3")

    def test_vm_dict_and_interop(self):
        src = 'use python: { json }\nrun main: { let o=json.loads("{\\"a\\": 7}"); print(o["a"]); }'
        self.assertEqual(self._vm_exec(src), "7")


class TestCodegen(unittest.TestCase):
    """AOT→Python 代码生成后端正确性。"""
    def _aot(self, src):
        from blacklang.codegen import compile_to_python, exec_compiled
        return exec_compiled(compile_to_python(parse(src))).strip()

    def test_aot_arith(self):
        self.assertEqual(self._aot("run main: { print(2 + 3 * 4); }"), "14")

    def test_aot_condition(self):
        out = self._aot("run main: { if 2 > 1: { print('y'); } else: { print('n'); } }")
        self.assertEqual(out, "y")

    def test_aot_forloop(self):
        self.assertEqual(self._aot("run main: { let s=0; for i in [1,2,3,4]: { s=s+i; } print(s); }"), "10")

    def test_aot_recursion(self):
        src = "fn fib(n: int) -> int: { if n<2: { return n; } return fib(n-1)+fib(n-2); } run main: { print(fib(20)); }"
        self.assertEqual(self._aot(src), "6765")

    def test_aot_logical(self):
        self.assertEqual(self._aot("run main: { if 1>0 and 2>1: { print('both'); } }"), "both")
        self.assertEqual(self._aot("run main: { if 0>1 or 2>1: { print('or'); } }"), "or")

    def test_aot_list_method(self):
        self.assertEqual(self._aot("run main: { let d=[1,2,3]; print(d.len()); print(d.sum()); }"), "3\n6")

    def test_aot_dict_interop(self):
        src = 'use python: { json }\nrun main: { let o=json.loads("{\\"k\\": 9}"); print(o["k"]); }'
        self.assertEqual(self._aot(src), "9")

    def test_aot_is_cpython_speed(self):
        # 编译路径应逼近 CPython（比率远低于解释器 ~50x，证明性能路径生效）
        import time
        import contextlib
        from blacklang.codegen import compile_to_python
        N = 26
        src = ('fn fib(n: int) -> int: { if n<2: { return n; } '
               'return fib(n-1)+fib(n-2); } run main: { print(fib(%d)); }' % N)
        py_src = compile_to_python(parse(src))
        ns = {}
        exec(compile(py_src, "<aot>", "exec"), ns)
        from blacklang.codegen import exec_compiled
        exec_compiled(py_src)  # 预热
        with contextlib.redirect_stdout(io.StringIO()):
            t = time.perf_counter()
            for _ in range(2):
                ns["main"]()
            aot = (time.perf_counter() - t) / 2

        def py_fib(n):
            return n if n < 2 else py_fib(n - 1) + py_fib(n - 2)
        py_fib(N)
        t = time.perf_counter()
        for _ in range(2):
            py_fib(N)
        py = (time.perf_counter() - t) / 2
        self.assertLess(aot / py, 5.0, f"AOT不应比等义 Python 慢超5倍, 得 {aot/py:.1f}x")


class TestSandbox(unittest.TestCase):
    """跨进程沙箱执行：隔离 + 超时墙 + 编译期拦截双保险。"""
    def _sb(self, src, timeout=3.0):
        from blacklang.sandbox import run_in_sandbox
        return run_in_sandbox(src, timeout=timeout)

    def test_sandbox_normal(self):
        r = self._sb('run main: { print(2+3); }')
        self.assertTrue(r["ok"])
        self.assertIn("5", r["stdout"])

    def test_sandbox_compile_reject(self):
        # 编译期越权，子进程也拒绝（双保险）
        r = self._sb('run main: { let x = http_get("http://x"); }')
        self.assertFalse(r["ok"])
        self.assertFalse(r.get("timed_out", False))
        self.assertTrue(any("越权" in e for e in r["errors"]))

    def test_sandbox_timeout_wall(self):
        if os.environ.get("BL_SKIP_SLOW"):
            self.skipTest("跳过慢测")
        src = '''fn fib(n: int) -> int: { if n<2: { return n; } return fib(n-1)+fib(n-2); }
run main: { let i=0; while i<200: { i=i+1; let z=fib(20); } print('done'); }'''
        r = self._sb(src, timeout=1.0)
        self.assertTrue(r.get("timed_out", False))
        self.assertFalse(r["ok"])

    def test_sandbox_runtime_cap(self):
        # 运行期越权（非编译期可完全证明的 interop），子进程拦截
        r = self._sb('run main: { let x = [1,2]; print(x[5]); }')
        self.assertFalse(r["ok"])


class TestEqualityAndConcat(unittest.TestCase):
    """==/!= 比较(统一为 Compare) + 字符串与数值拼接(AI 友好)。"""
    def _vm(self, src):
        from blacklang.vm import run_vm
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            run_vm(parse(src))
        return out.getvalue().strip()

    def test_eq_compare(self):
        self.assertEqual(self._vm("run main: { if 2 == 2: { print(1); } else: { print(0); } }"), "1")
        self.assertEqual(self._vm("run main: { if 2 != 3: { print(1); } else: { print(0); } }"), "1")
        self.assertEqual(self._vm("run main: { if 2 == 3: { print(1); } else: { print(0); } }"), "0")

    def test_eq_in_treewalk(self):
        from blacklang.evaluator import Evaluator
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            Evaluator(parse("run main: { if \"a\" == \"a\": { print('eq'); } }")).run()
        self.assertEqual(out.getvalue().strip(), "eq")

    def test_nested_for_vm(self):
        # 嵌套 for + 去重（回归：VM 隐藏槽位冲突）
        src = ('run main: { let src=[]; src.append("a"); src.append("b"); src.append("a"); '
               'let uniq=[]; for x in src { let seen=false; for y in uniq { if y==x { seen=true; } } '
               'if seen==false { uniq.append(x); } } print(uniq.len()); }')
        self.assertEqual(self._vm(src), "2")

    def test_string_plus_number(self):
        self.assertEqual(self._vm("run main: { let n=3; print(\"v=\" + n); }"), "v=3")
        self.assertEqual(self._vm("run main: { let f=1.5; print(\"x\" + f); }"), "x1.5")

    def test_str_builtin(self):
        from blacklang.evaluator import Evaluator
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            Evaluator(parse('run main: { print("[" + str(42) + "]"); }')).run()
        self.assertEqual(out.getvalue().strip(), "[42]")

    def test_aot_eq(self):
        from blacklang.codegen import compile_to_python, exec_compiled
        out = exec_compiled(compile_to_python(parse(
            "run main: { let s=0; for i in [1,2,3]: { if i==2 { s=s+10; } } print(s); }")))
        self.assertEqual(out.strip(), "10")


class TestBusinessDemo(unittest.TestCase):
    """端到端业务 demo：读 JSON → 清洗 → 聚合 → 报表。"""
    def _run(self, src):
        from blacklang.vm import run_vm
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            run_vm(parse(src))
        return out.getvalue()

    def test_business_pipeline(self):
        here = os.path.dirname(os.path.abspath(__file__))
        demo = os.path.join(here, "..", "examples", "business_demo", "src", "main.bl")
        with open(demo, encoding="utf-8") as f:
            src = f.read()
        out = self._run(src)
        self.assertIn("原始订单数 = 8", out)
        self.assertIn("有效订单数 = 6", out)
        self.assertIn("电子 : 8095.0", out)
        self.assertIn("家纺 : 497.9", out)
        self.assertIn("食品 : 255.0", out)
        self.assertIn("总销售额 = 8847.9", out)


class TestAiTool(unittest.TestCase):
    """LangChain 形态的 blacklang_tool：编译自检 + 运行，越权拦截。"""
    def test_tool_ok(self):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "examples"))
        from blacklang_tool import blacklang_tool
        r = blacklang_tool({"code": "run main: { let s=0; for i in [1,2,3,4]: { s=s+i; } print(s); }"})
        self.assertTrue(r["ok"])
        self.assertIn("10", r["stdout"])

    def test_tool_sandbox_timeout(self):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "examples"))
        from blacklang_tool import blacklang_tool
        # 死循环会被运行期/超时墙拦截，不返回 ok
        r = blacklang_tool({"code": 'run main: { while 1>0 { print("x"); } }'})
        self.assertFalse(r.get("ok", True))

    def test_tool_rejects_unauthorized_net(self):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "examples"))
        from blacklang_tool import blacklang_tool
        r = blacklang_tool({"code": 'run main: { let x = http_get("http://bad"); }'})
        self.assertFalse(r["ok"])
        self.assertTrue(r.get("intercepted", False))
        self.assertTrue(any("越权" in e for e in r["errors"]))


class TestCBackend(unittest.TestCase):
    """AOT→C 后端：真实编译为本机码并运行。"""
    @classmethod
    def setUpClass(cls):
        from blacklang.cbackend import c_available
        cls.cc = c_available()

    def _c(self, src):
        self.assertIsNotNone(self.cc, "无 C 编译器，跳过")
        from blacklang.cbackend import compile_to_c, build_and_run
        r = build_and_run(compile_to_c(parse(src)))
        self.assertTrue(r["ok"], f"C 运行失败: {r['errors']} {r.get('cc_out','')}")
        return r["stdout"].strip()

    def test_c_available(self):
        from blacklang.cbackend import c_available
        self.assertIsNotNone(c_available())

    def test_c_arith(self):
        self.assertEqual(self._c("run main: { print(2 + 3 * 4); }"), "14")

    def test_c_true_division(self):
        # BL 的 '/' 是真除法
        self.assertEqual(self._c("run main: { print(10 / 4); }"), "2.5")

    def test_c_recursion(self):
        src = ("fn fib(n: int) -> int: { if n<2: { return n; } "
               "return fib(n-1)+fib(n-2); } run main: { print(fib(22)); }")
        self.assertEqual(self._c(src), "17711")

    def test_c_while_loop(self):
        self.assertEqual(self._c("run main: { let s=0; let i=0; while i<1000: { s=s+i; i=i+1; } print(s); }"), "499500")

    def test_c_for_list(self):
        self.assertEqual(self._c("run main: { let s=0; for i in [1,2,3,4,5]: { s=s+i; } print(s); }"), "15")

    def test_c_string_concat(self):
        self.assertEqual(self._c('run main: { let n="BL"; print("hi " + n); }'), "hi BL")

    def test_c_string_plus_number(self):
        self.assertEqual(self._c('run main: { let n=3; print("n=" + n); }'), "n=3")

    def test_c_float_format(self):
        self.assertEqual(self._c("run main: { print(1.5 * 2 + 1); }"), "4.0")

    def test_c_mod(self):
        self.assertEqual(self._c("run main: { print(10 % 3); }"), "1")
        self.assertEqual(self._c("run main: { print(10.5 % 3); }"), "1.5")

    def test_c_logic(self):
        self.assertEqual(self._c('run main: { if 1>0 and 2>1: { print("both"); } }'), "both")
        self.assertEqual(self._c('run main: { if 0>1 or 2>1: { print("or"); } }'), "or")

    def test_c_unsupported_interop_raises(self):
        from blacklang.cbackend import c_available, compile_to_c, CBackendError
        with self.assertRaises(CBackendError):
            compile_to_c(parse('use python: { math }\nrun main: { print(math.sqrt(4)); }'))


class TestDiagnostics(unittest.TestCase):
    """结构化诊断（机器可读）。"""
    def test_capability_diag(self):
        from blacklang.diagnostics import collect
        d = collect('run main: { let x = http_get("u"); }')
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["code"], "BL1001")
        self.assertEqual(d[0]["stage"], "capability")
        self.assertTrue(d[0]["hint"])
        self.assertEqual(d[0]["severity"], "error")

    def test_type_diag_undefined(self):
        from blacklang.diagnostics import collect
        d = collect("run main: { print(nope); }")
        self.assertEqual(d[0]["code"], "BL1002")
        self.assertEqual(d[0]["stage"], "type")

    def test_parse_diag_has_line_col(self):
        from blacklang.diagnostics import collect
        d = collect("run main: { print(1 }")
        self.assertEqual(d[0]["code"], "BL0001")
        self.assertIsNotNone(d[0]["line"])

    def test_ok_has_no_errors(self):
        from blacklang.diagnostics import collect, has_errors, render
        d = collect("run main: { print(1); }")
        self.assertFalse(has_errors(d))
        self.assertIn("通过", render(d))

    def test_json_render(self):
        import json
        from blacklang.diagnostics import collect, render
        out = render(collect('run main: { let x = http_get("u"); }'), as_json=True)
        obj = json.loads(out)
        self.assertFalse(obj["ok"])
        self.assertEqual(obj["diagnostics"][0]["code"], "BL1001")


class TestNativeAndApi(unittest.TestCase):
    """CLI/API 的 native 路径与回退。"""
    def test_api_native_run(self):
        import blacklang as bl
        r = bl.native_run("run main: { let s=0; for i in [1,2,3]: { s=s+i; } print(s); }")
        self.assertTrue(r["ok"])
        self.assertIn("6", r["stdout"])

    def test_api_native_fallback_on_interop(self):
        import blacklang as bl
        r = bl.native_run('use python: { math }\nrun main: { print(math.sqrt(16)); }')
        self.assertTrue(r["ok"])
        self.assertTrue(r.get("fallback"))

    def test_api_diagnostics(self):
        import blacklang as bl
        r = bl.diagnose("run main: { print(undeclared); }")
        self.assertFalse(r["ok"])
        self.assertEqual(r["diagnostics"][0]["code"], "BL1002")

    def test_exports(self):
        import blacklang as bl
        for name in ("native_run", "diagnose", "run_vm", "InteropBus"):
            self.assertTrue(hasattr(bl, name), name)


class TestCrossPathConsistency(unittest.TestCase):
    """同一份源码在 VM / AOT→Python / AOT→C 三条路径输出应一致。"""
    PROGRAMS = [
        "run main: { print(2 + 3 * 4); }",
        "run main: { print(10 / 4); }",
        "run main: { print(0.1 + 0.2); }",
        "run main: { print(1.0 / 3.0); }",
        "run main: { print(1.5 * 2 + 1); }",
        "run main: { print(10 % 3); }",
        "run main: { print(10.5 % 3); }",
        'run main: { let n=3; print("n=" + n); }',
        'run main: { let x="BL"; print("hi " + x); }',
        "run main: { let s=0; for i in [1,2,3,4,5]: { s=s+i; } print(s); }",
        "run main: { let s=0; let i=0; while i<100: { s=s+i; i=i+1; } print(s); }",
        ("fn fib(n: int) -> int: { if n<2: { return n; } "
         "return fib(n-1)+fib(n-2); } run main: { print(fib(18)); }"),
        'run main: { if 1>0 and 2>1: { print("both"); } }',
        'run main: { if 3 > 2: { print("yes"); } else: { print("no"); } }',
    ]

    def _outputs(self, src):
        from blacklang.vm import run_vm
        from blacklang.codegen import compile_to_python, exec_compiled
        from blacklang.cbackend import compile_to_c, build_and_run, c_available
        vm_out = io.StringIO()
        with contextlib.redirect_stdout(vm_out):
            run_vm(parse(src))
        py_out = exec_compiled(compile_to_python(parse(src)))
        outs = {"vm": vm_out.getvalue(), "py": py_out}
        if c_available():
            r = build_and_run(compile_to_c(parse(src)))
            self.assertTrue(r["ok"], r.get("cc_out", ""))
            outs["c"] = r["stdout"]
        from blacklang.llvmbackend import llvm_available, compile_to_llvm_ir, build_and_run_ir
        if llvm_available():
            r2 = build_and_run_ir(compile_to_llvm_ir(parse(src)))
            self.assertTrue(r2["ok"], (r2.get("cc_out") or "")[:400])
            outs["llvm"] = r2["stdout"]
        return outs

    def test_all_paths_agree(self):
        for src in self.PROGRAMS:
            outs = self._outputs(src)
            vals = set(outs.values())
            self.assertEqual(
                len(vals), 1,
                f"路径输出不一致 for {src!r}: {outs}")


class TestLLVMBackend(unittest.TestCase):
    """AOT→LLVM IR → clang 编译为本机码并运行。"""
    @classmethod
    def setUpClass(cls):
        from blacklang.llvmbackend import llvm_available
        cls.cc = llvm_available()

    def _llvm(self, src):
        self.assertIsNotNone(self.cc, "无 clang，跳过")
        from blacklang.llvmbackend import compile_to_llvm_ir, build_and_run_ir
        r = build_and_run_ir(compile_to_llvm_ir(parse(src)))
        self.assertTrue(r["ok"], f"LLVM 运行失败: {r['errors']} {(r.get('cc_out') or '')[:400]}")
        return r["stdout"].strip()

    def test_llvm_arith(self):
        self.assertEqual(self._llvm("run main: { print(2 + 3 * 4); }"), "14")

    def test_llvm_true_division(self):
        self.assertEqual(self._llvm("run main: { print(10 / 4); }"), "2.5")

    def test_llvm_recursion(self):
        src = ("fn fib(n: int) -> int: { if n<2: { return n; } "
               "return fib(n-1)+fib(n-2); } run main: { print(fib(22)); }")
        self.assertEqual(self._llvm(src), "17711")

    def test_llvm_while(self):
        self.assertEqual(self._llvm("run main: { let s=0; let i=0; while i<1000: { s=s+i; i=i+1; } print(s); }"), "499500")

    def test_llvm_for_list(self):
        self.assertEqual(self._llvm("run main: { let s=0; for i in [1,2,3,4,5]: { s=s+i; } print(s); }"), "15")

    def test_llvm_string_concat(self):
        self.assertEqual(self._llvm('run main: { let n="BL"; print("hi " + n); }'), "hi BL")

    def test_llvm_string_plus_number(self):
        self.assertEqual(self._llvm('run main: { let n=3; print("n=" + n); }'), "n=3")

    def test_llvm_float_shortest_repr(self):
        self.assertEqual(self._llvm("run main: { print(0.1 + 0.2); }"), "0.30000000000000004")
        self.assertEqual(self._llvm("run main: { print(1.0 / 3.0); }"), "0.3333333333333333")
        self.assertEqual(self._llvm("run main: { print(1.5 * 2 + 1); }"), "4.0")

    def test_llvm_mod(self):
        self.assertEqual(self._llvm("run main: { print(10 % 3); }"), "1")
        self.assertEqual(self._llvm("run main: { print(10.5 % 3); }"), "1.5")

    def test_llvm_logic_and_if(self):
        self.assertEqual(self._llvm('run main: { if 1>0 and 2>1: { print("both"); } }'), "both")
        self.assertEqual(self._llvm('run main: { if 3>2: { print("yes"); } else: { print("no"); } }'), "yes")

    def test_llvm_multiarg_print(self):
        self.assertEqual(self._llvm("run main: { print(1, 2.5, \"z\"); }"), "1 2.5 z")

    def test_llvm_early_return(self):
        src = "fn f(n) { if n>0 { return 1; } return 0; }\nrun main: { print(f(5)); print(f(-1)); }"
        self.assertEqual(self._llvm(src), "1\n0")

    def test_emit_llvm_ir_text(self):
        from blacklang.llvmbackend import compile_to_llvm_ir
        ir = compile_to_llvm_ir(parse("run main: { print(1); }"))
        self.assertIn("define i64 @bl_main()", ir)
        self.assertIn("define i32 @main()", ir)
        self.assertIn("bl_concat", ir)

    def test_llvm_unsupported_interop_raises(self):
        from blacklang.llvmbackend import compile_to_llvm_ir
        from blacklang.cbackend import CBackendError
        with self.assertRaises(CBackendError):
            compile_to_llvm_ir(parse('use python: { math }\nrun main: { print(math.sqrt(4)); }'))


class TestLLVMJIT(unittest.TestCase):
    """进程内 LLVM JIT（需 llvmlite 可加载）。"""
    def test_jit_if_available(self):
        from blacklang.llvmbackend import jit_available, run_llvm
        if not jit_available():
            self.skipTest("llvmlite 不可用（本机 Python 无法加载第三方 dylib）")
        r = run_llvm(parse("fn fib(n: int) -> int: { if n<2: { return n; } "
                           "return fib(n-1)+fib(n-2); } run main: { print(fib(20)); }"),
                     use_jit=True)
        self.assertTrue(r["ok"], r["errors"])
        self.assertIn("6765", r["stdout"])
        self.assertTrue(r.get("jit"))

    def test_ptr_mode_selection(self):
        from blacklang.llvmbackend import jit_ptr_mode, llvm_jit_version
        v = llvm_jit_version()
        if v is None:
            self.skipTest("llvmlite 不可用")
        self.assertEqual(jit_ptr_mode(), "opaque" if v >= 16 else "typed")


class TestLetScoping(unittest.TestCase):
    """`let` = 声明：函数内为真正的局部变量，递归可重入（本轮修复的 VM bug）。"""
    REENTRANT = (
        "fn f(n) {\n"
        "  let t = n * 2;\n"
        "  if n > 1 {\n"
        "    let r = f(n - 1);\n"
        "    return t + r;\n"
        "  }\n"
        "  return t;\n"
        "}\n"
        "run main: { print(f(3)); }"
    )

    def test_parser_marks_let_as_decl(self):
        from blacklang.ast_nodes import Assign, If, While, ForIn, Sandbox
        globals()["Assign"] = Assign
        globals()["If"] = If
        globals()["While"] = While
        globals()["ForIn"] = ForIn
        globals()["Sandbox"] = Sandbox
        prog = parse("run main: { let x = 1; x = 2; }")
        assigns = []

        def walk(ss):
            for s in ss:
                if isinstance(s, Assign):
                    assigns.append(s)
                elif isinstance(s, If):
                    for _, b in s.branches:
                        walk(b)
                elif isinstance(s, While):
                    walk(s.body)
                elif isinstance(s, ForIn):
                    walk(s.body)
                elif isinstance(s, Sandbox):
                    walk(s.body)

        walk(prog.entries)
        main_block = assigns or []
        self.assertTrue(main_block, "未找到赋值语句")
        self.assertTrue(main_block[0].is_decl, "`let x` 应标记为声明")
        if len(main_block) > 1:
            self.assertFalse(main_block[1].is_decl, "`x = 2` 应为重新赋值")

    def test_vm_reentrant_let(self):
        from blacklang.vm import run_vm
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            run_vm(parse(self.REENTRANT))
        self.assertEqual(out.getvalue().strip(), "12")

    def test_all_backends_agree_on_reentrancy(self):
        outs = {}
        from blacklang.vm import run_vm
        o = io.StringIO()
        with contextlib.redirect_stdout(o):
            run_vm(parse(self.REENTRANT))
        outs["vm"] = o.getvalue()
        from blacklang.codegen import compile_to_python, exec_compiled
        outs["py"] = exec_compiled(compile_to_python(parse(self.REENTRANT)))
        from blacklang.llvmbackend import llvm_available, compile_to_llvm_ir, build_and_run_ir
        if llvm_available():
            r = build_and_run_ir(compile_to_llvm_ir(parse(self.REENTRANT)))
            self.assertTrue(r["ok"], (r.get("cc_out") or "")[:300])
            outs["llvm"] = r["stdout"]
        self.assertEqual(len(set(outs.values())), 1, f"路径不一致: {outs}")
        self.assertEqual(outs["vm"].strip(), "12")
