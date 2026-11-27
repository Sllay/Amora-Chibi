"""Verify the immutable, model-only copy against the original artifact's file digests."""
import hashlib,json,shutil,sys
from pathlib import Path,PurePosixPath
EXPECTED={"AmoraBolinha.cdi3.json":"76f00257932b3f8ec70a915fea566b67b0004dc64d636b0144392f6c8d87c2e5","AmoraBolinha.moc3":"dd23be7602621ea558b3bca17e2a23ebadb4b5d9b5a08eb73b96169b114e5cc0","AmoraBolinha.model3.json":"29cb46315757bbf59aecd71172f036962de7c7d33aa50d044eefa76724eb7262","AmoraBolinha.physics3.json":"a3b9ad022f628ed7d9216104234f134bb0e06fbe4fe7bce122b42d6473d2fff7","AmoraBolinha.textures/texture_00.png":"83727334c3ba42a638224ac61cea9ac627d7902cef1df295f48c5ec8e1f91b64","AmoraBolinha.textures/texture_01.png":"eb215cfa03b31af4b438532e72d2496efeb417de4900cb66f17e92a4efaa9260","AmoraBolinha.textures/texture_02.png":"d618bc1fe3057055b5abc2fff50506a5c0425fd64d28720f8872ef4328639da2","AmoraBolinha.textures/texture_03.png":"3da2517e848702b9e022d4cf35c48adbab8bb4bf9a3e5f6bd1639982c3727def","AmoraBolinha.textures/texture_04.png":"90844f6773d8b5ff6bc8df909e8abe15a425abf0a4d3f061353db7097ee6ba71","AmoraBolinha.textures/texture_05.png":"b657b83faf6b77eea6e996c6530ef7daeb0559d8b71135d2ff85ec728e9b05c9","AmoraBolinha.textures/texture_06.png":"b1ebecbd1168cab127a035377f20a45870ba9f448fc60258d3e8e03434d890fa","AmoraBolinha.textures/texture_07.png":"40e3a0c617dc5666ff38e9242ef5113cd5f55443cdb9620cd70c8b052ace95d1","AmoraBolinha.textures/texture_08.png":"b9491e65d8ebdafc29fafb1075af43a32ba0dd18d52e68ae1e474652e016f053","AmoraBolinha.textures/texture_09.png":"b6426e5ffc85cadd1c6053875b40dfce0c5c70a9a652fd09f2247d6156e17871","AmoraBolinha.textures/texture_10.png":"f1c7d24e9881df0015a6ada916eadb52882bc055109e2cd2dc76c382dccb58c9","AmoraBolinha.textures/texture_11.png":"cdcb2fa158d06a56c1519a0b054d9f9b6452f63ed81c4e85d9f669c063ae3eca","AmoraBolinha.textures/texture_12.png":"9035521aa38611f8d9a6fe3eee984a64c38f8f2839b14268c54a81f613e22c9b","AmoraBolinha.textures/texture_13.png":"4d9e51daff7201481ba5d3d90e50adf0ab258c3f1673fd53f7e143113020f4e9","AmoraBolinha.textures/texture_14.png":"2991538c152a9746b14400d2409603ce129edc1afd395bbf7c6dd9276139bb75","AmoraBolinha.textures/texture_15.png":"8cb9250935461bda882d4419a6d2560fd12b5010cf07ad6cff80a55f6a3d0fb3","AmoraBolinha.textures/texture_16.png":"2cddb9aa4d7dc8f68cb0589ef32b370f47759f7a531d97bd06ff9b33517a193b","AmoraBolinha.textures/texture_17.png":"e48188fcfe9fbdd34b90fa1833daa9c811e69f2712312ddbf02857bf327c7a60","AmoraBolinha.textures/texture_18.png":"aa971162e796204d25e2b37cd3f1c30a3d0d1416bd99512ea1f697703a24e39d","AmoraBolinha.textures/texture_19.png":"e23a6c5614576b595323b407a11ffcbf83f5481f92f3f5036531f8e3d33a9695","AmoraBolinha.textures/texture_20.png":"a11d282393cef8fd70a9f3c788bcfeffdb9dd945d04fd3c1d8c9e7d4aabdb295","AmoraBolinha.textures/texture_21.png":"5bf90c07809ec4430d7d75fd1b63eb5e70204a6b78d92c73b53b0b2211c1c250","expressions/AmoraBolinha_Angry.exp3.json":"bf207051af3471faf718ef3fcf994b0fa8ae53a3026694dddda9871ee740c4df","expressions/AmoraBolinha_Annoyed.exp3.json":"653d62e84ad26ecc866b64c3283951a4569fd0fe0a0939dbeceb35da80b8c5ab","expressions/AmoraBolinha_Blushing.exp3.json":"ae190deb1ef158e19c1338fea702cc9356f5e9d9a0910ccdacb014b327bd56de","expressions/AmoraBolinha_Crying.exp3.json":"6ea59867e81c8a0a0deccfe6363b9525236d2e3ba8aed8c4b766f8f0effa795c","expressions/AmoraBolinha_Curious.exp3.json":"73ca90bb2fc39316f222c03ff2843e60d9446cd18069e6739d4d56e92a393ec5","expressions/AmoraBolinha_Deadpan.exp3.json":"72e36eb7c7fdba1961bdbd4029c79412c35b457c8ea0ffb762ba624dd3aa6d5d","expressions/AmoraBolinha_Enchanted.exp3.json":"b4083bfb8aa466a534121e30b37dda169b359b00e292b447bbf2023efbe1aeb9","expressions/AmoraBolinha_Happy.exp3.json":"f8b82fe71f2d2723bab09c35012eade9e88c35943e050856803923c275e9a07e","expressions/AmoraBolinha_LaughingTears.exp3.json":"05adef9f20cccc81a1ac9cf00978d36292e15a4e62e9cd0d5f5b18396af6d868","expressions/AmoraBolinha_Neutral.exp3.json":"6bfc4910cb41b56416433eff28106a30d8fba21285e0d4ac7fe691a2d9e0b87f","expressions/AmoraBolinha_Sad.exp3.json":"0db76aeb6a7aea5a7cd2827726d6c74d4d1fcff59f455d02e909ab0e953e9bf2","expressions/AmoraBolinha_Shouting.exp3.json":"de6034dc9f0cc108328ce75ba5a8850dd1c713dcabdb798cbb17262a1d1db32c","expressions/AmoraBolinha_Shy.exp3.json":"2287f05dbc96ee10fd81ba4aa093446eaf7eb9f9866519fe283dbe3d8a7b48f3","expressions/AmoraBolinha_Sleepy.exp3.json":"ae7ba08ebb6f5462cf737d028d8f74d5f444c7dce0c3aae6b2e074de95e2840b","expressions/AmoraBolinha_Sobbing.exp3.json":"4962a004e712ba9a70663bc2879c2cadba7235482f0f73310b9bce8f2130f0c8","expressions/AmoraBolinha_Surprised.exp3.json":"602e2f79893e48ad305f96d9b85b6b2b43f866a84c1e6b48be187b8ba0c49148","expressions/AmoraBolinha_WinkLeft.exp3.json":"197754ca45fecc72c5a6e5818bdef5ab424dff00ccbe9b0e0fe9b3476d181d27","expressions/AmoraBolinha_WinkRight.exp3.json":"17f14a608834c250e1df0889269dc831ec817a0108462e8d0d279c2071c1c0f0","motions/AmoraBolinha_IdleTest.motion3.json":"af09fc57510d0e304eb0ee7e680beae82da3893044a226978a472162462784d5"}
SOURCE="333b935d12e7b82397efbc4d797c471e1aec05f1"
def copy_verified(root,out):
    root=Path(root)
    out=Path(out)
    manifest=json.loads((root/"rig-integrity.json").read_text())
    if manifest != {"source":SOURCE,"artifact":14986551731,"files":EXPECTED}:
        raise ValueError("manifest differs")
    actual=set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("symlink")
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    if actual != set(EXPECTED)|{"rig-integrity.json"}:
        raise ValueError("unexpected files")
    for name,digest in EXPECTED.items():
        rel=PurePosixPath(name)
        if rel.is_absolute() or ".." in rel.parts or rel.suffix.lower() not in {".json",".png",".moc3"}:
            raise ValueError("invalid model path")
        with (root/name).open("rb") as stream:
            if hashlib.file_digest(stream,"sha256").hexdigest()!=digest:
                raise ValueError("file digest mismatch")
    out.mkdir(parents=True,exist_ok=False)
    for name in EXPECTED:
        dest=out/name
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(root/name,dest)
if __name__=="__main__":
    try:
        copy_verified(sys.argv[1],sys.argv[2])
    except (ValueError,OSError,KeyError,TypeError):
        raise SystemExit("VISUAL_V5_RIG_INTEGRITY_FAILED") from None
    print("VISUAL_V5_RIG_VERIFIED files="+str(len(EXPECTED)))
