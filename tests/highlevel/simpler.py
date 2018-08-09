from seamless.highlevel import Context

ctx = Context()
ctx.mount("/tmp/mount-test")

ctx.a = 12

def triple_it(a):
    return 3 * a

def triple_it2(a, b):
    return 3 * a + b

ctx.transform = triple_it
ctx.transform.a = ctx.a
ctx.myresult = ctx.transform
ctx.transform.code = triple_it2
ctx.tfcode >> ctx.transform.code
ctx.transform.b = 100
ctx.tfcode = triple_it2
ctx.equilibrate()
print(ctx.myresult.value)
print("START")
ctx.equilibrate()