import axios from "axios";
import dotenv from "dotenv";

dotenv.config();

async function main() {
  try {
    const res = await axios.get(
      "https://api.pinterest.com/v5/user_account",
      {
        headers: {
          Authorization: `Bearer ${process.env.PINTEREST_ACCESS_TOKEN}`,
        },
      }
    );

    console.log("SUCCESS");
    console.log(res.data);
  } catch (err: any) {
    console.log("ERROR");
    console.log(err.response?.data || err.message);
  }
}

main();